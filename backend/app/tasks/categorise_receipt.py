"""Turn a parsed receipt into a categorised expense.

This is the second half of the pipeline: ``process_receipt`` populates
``parsed_payload`` and marks the row ``parsed``; this task picks a
category for the merchant and creates the matching ``Expense`` row,
then advances the receipt to ``categorised``.

Why a separate task (vs. inlining at the end of ``process_receipt``):

* **Independent retry.** OCR failures and LLM failures have different
  shapes — Tesseract not reading bytes is permanent; an OpenAI 429 is
  transient. Splitting the tasks lets each retry on its own clock.
* **Reprocess corrections cheaply.** When a user corrects a category
  we may eventually want to re-run categorisation across their
  historical receipts (without re-OCRing). Having categorisation as a
  standalone task makes that a one-line dispatch.
* **Failure isolation.** A buggy LLM prompt change can't take down
  the OCR worker.

Idempotent at every level: tasks may be retried, the upstream task
may double-enqueue, and the worker may pick the same id twice. The
unique constraint on ``expenses.receipt_id`` enforces "one expense per
receipt"; we check before inserting so the second invocation is a
no-op rather than a constraint-violation retry storm.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from datetime import date as date_t
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.enums import ExpenseSource, ReceiptStatus
from app.models.expense import Expense
from app.models.receipt import Receipt
from app.services.categorisation import categorise_merchant
from app.tasks.celery_app import celery_app

log = structlog.get_logger()

# Used when the parser couldn't extract a merchant name. Surfacing
# something rather than silently dropping the row keeps the expense
# visible in the user's list — they can edit the merchant later.
_DEFAULT_MERCHANT = "Unknown"
_DEFAULT_CURRENCY = "USD"


@celery_app.task(name="spendlens.categorise_receipt", bind=True, max_retries=3)
def categorise_receipt(self: Any, receipt_id: str) -> None:
    """Entry point Celery dispatches to. Runs the async body."""
    try:
        _run_sync(_run(receipt_id))
    except Exception as exc:  # noqa: BLE001 — surface every error to retry
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc


def _run_sync(coro: Coroutine[Any, Any, None]) -> None:
    """Run an async coroutine to completion from a sync context.

    Same shape as the helper in :mod:`app.tasks.process_receipt` —
    detects an existing event loop (pytest-asyncio eager mode) and
    farms the work out to a worker thread that owns its own loop.
    Production has no running loop and takes the fast path.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, coro).result()


@asynccontextmanager
async def _task_session() -> AsyncIterator[AsyncSession]:
    """Per-invocation DB session bound to the task's own event loop.

    asyncpg connections are loop-bound; reusing the module-global
    engine across separate ``asyncio.run`` calls strands connections
    on closed loops. Building one engine per task and disposing it
    inside the loop's lifetime is the cleanest fix.
    """
    engine = create_async_engine(str(get_settings().database_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _run(receipt_id_str: str) -> None:
    receipt_id = UUID(receipt_id_str)

    async with _task_session() as session:
        receipt = (
            await session.execute(select(Receipt).where(Receipt.id == receipt_id))
        ).scalar_one_or_none()
        if receipt is None:
            log.warning("categorise.missing", receipt_id=receipt_id_str)
            return

        # Idempotency guard #1: row already has an expense. Either the
        # task ran before or two enqueues raced. Make sure the receipt
        # status reflects ``categorised`` and bail.
        existing = (
            await session.execute(select(Expense).where(Expense.receipt_id == receipt_id))
        ).scalar_one_or_none()
        if existing is not None:
            if receipt.status != ReceiptStatus.CATEGORISED:
                receipt.status = ReceiptStatus.CATEGORISED
                await session.commit()
            return

        # Idempotency guard #2: only categorise rows that the OCR step
        # actually parsed. Anything else (still UPLOADED, FAILED) means
        # there's nothing to categorise — log and stop, don't retry.
        if receipt.status != ReceiptStatus.PARSED:
            log.warning(
                "categorise.not_parsed",
                receipt_id=receipt_id_str,
                status=receipt.status.value,
            )
            return

        merchant, amount, expense_date = _coerce_payload(receipt.parsed_payload or {})

        category = await categorise_merchant(
            session,
            user_id=receipt.user_id,
            merchant=merchant,
        )

        expense = Expense(
            user_id=receipt.user_id,
            merchant_name=merchant,
            amount=amount,
            currency=_DEFAULT_CURRENCY,
            category=category,
            expense_date=expense_date,
            receipt_id=receipt.id,
            source=ExpenseSource.RECEIPT,
        )
        session.add(expense)
        receipt.status = ReceiptStatus.CATEGORISED
        await session.commit()
        log.info(
            "categorise.done",
            receipt_id=receipt_id_str,
            merchant=merchant,
            category=category.value,
        )


def _coerce_payload(payload: dict[str, Any]) -> tuple[str, Decimal, date_t]:
    """Pull merchant/amount/date from the parser's JSONB blob.

    Each field is permissive — the parser surrenders to ``None`` on
    ambiguity, and we'd rather create a placeholder expense the user
    can edit than block the entire pipeline on a missed regex. The
    receipt's own row carries the full ``parsed_payload`` so they can
    always cross-reference the source.
    """
    merchant = payload.get("merchant") or _DEFAULT_MERCHANT

    total_str = payload.get("total")
    try:
        amount = Decimal(total_str) if total_str else Decimal("0.00")
    except (InvalidOperation, TypeError):
        amount = Decimal("0.00")

    date_str = payload.get("transaction_date")
    try:
        expense_date = date_t.fromisoformat(date_str) if date_str else date_t.today()
    except (ValueError, TypeError):
        expense_date = date_t.today()

    return merchant, amount, expense_date

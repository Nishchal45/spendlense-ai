"""Integration tests for the categorise-receipt task.

The task reads a ``parsed`` receipt, picks a category, creates the
matching ``Expense`` row, and advances the receipt to ``categorised``.
We use the ``committed_session`` pattern (same shape as the OCR
pipeline tests) because the task opens its own DB session in a worker
thread — a transactional fixture is invisible to that connection.

Categorisation itself is exercised as the rule-based path; the LLM is
patched out via an environment variable check (``OPENAI_API_KEY``
unset → never called). This file's job is verifying the **task
orchestration**, not the categoriser.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.enums import ExpenseCategory, ExpenseSource, ReceiptStatus
from app.models.expense import Expense
from app.models.receipt import Receipt
from app.models.user import User
from app.services.user_service import create_user
from app.tasks.categorise_receipt import categorise_receipt


@pytest_asyncio.fixture
async def committed_session() -> AsyncIterator[AsyncSession]:
    """Real-commit session. Cleans up by deleting users at teardown."""
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created_user_ids: list[UUID] = []
    session = factory()
    session.info["created_user_ids"] = created_user_ids
    try:
        yield session
    finally:
        await session.close()
        if created_user_ids:
            async with factory() as cleanup:
                await cleanup.execute(delete(User).where(User.id.in_(created_user_ids)))
                await cleanup.commit()
        await engine.dispose()


async def _create_user(session: AsyncSession, email: str) -> User:
    user = await create_user(session, email=email, password="hunter2hunter2")
    session.info["created_user_ids"].append(user.id)
    return user


async def _seed_parsed_receipt(
    session: AsyncSession,
    *,
    user_id: UUID,
    merchant: str,
    total: str = "9.99",
    transaction_date: str = "2026-04-25",
) -> Receipt:
    """Insert a receipt that's already in the ``parsed`` state.

    Skips the OCR step entirely so the test can focus on the
    categorise-and-create-expense logic. The ``parsed_payload`` mirrors
    what the parser would produce for a real receipt.
    """
    receipt = Receipt(
        user_id=user_id,
        storage_key=f"tests/{uuid4()}.jpg",
        mime_type="image/jpeg",
        file_size_bytes=128,
        status=ReceiptStatus.PARSED,
        raw_text=f"{merchant}\nTotal {total}\n",
        parsed_payload={
            "merchant": merchant,
            "total": total,
            "transaction_date": transaction_date,
            "line_items": [],
        },
    )
    session.add(receipt)
    await session.commit()
    await session.refresh(receipt)
    return receipt


@pytest.fixture
async def user(committed_session: AsyncSession) -> User:
    return await _create_user(committed_session, f"cat-task-{uuid4()}@example.com")


class TestCategoriseReceiptHappyPath:
    async def test_creates_expense_with_rule_based_category(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        receipt = await _seed_parsed_receipt(
            committed_session, user_id=user.id, merchant="Starbucks #42", total="4.75"
        )

        # Eager mode runs the task body synchronously.
        categorise_receipt.delay(str(receipt.id))

        await committed_session.refresh(receipt)
        assert receipt.status == ReceiptStatus.CATEGORISED

        expense = (
            await committed_session.execute(select(Expense).where(Expense.receipt_id == receipt.id))
        ).scalar_one()
        assert expense.merchant_name == "Starbucks #42"
        assert expense.amount == Decimal("4.75")
        assert expense.category == ExpenseCategory.FOOD_DINING
        assert expense.source == ExpenseSource.RECEIPT
        assert expense.expense_date == date(2026, 4, 25)


class TestIdempotency:
    async def test_running_twice_does_not_double_create(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        receipt = await _seed_parsed_receipt(
            committed_session, user_id=user.id, merchant="Lyft Ride"
        )

        categorise_receipt.delay(str(receipt.id))
        categorise_receipt.delay(str(receipt.id))

        rows = (
            (
                await committed_session.execute(
                    select(Expense).where(Expense.receipt_id == receipt.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(list(rows)) == 1


class TestStatusGuards:
    async def test_skips_when_status_is_not_parsed(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # An UPLOADED row (OCR hasn't run yet) should be left alone.
        # Categorising it would mean creating an expense without a
        # parsed merchant — exactly what the guard prevents.
        receipt = await _seed_parsed_receipt(
            committed_session, user_id=user.id, merchant="Whole Foods"
        )
        receipt.status = ReceiptStatus.UPLOADED
        await committed_session.commit()

        categorise_receipt.delay(str(receipt.id))

        await committed_session.refresh(receipt)
        assert receipt.status == ReceiptStatus.UPLOADED  # unchanged
        rows = (
            (
                await committed_session.execute(
                    select(Expense).where(Expense.receipt_id == receipt.id)
                )
            )
            .scalars()
            .all()
        )
        assert list(rows) == []

    async def test_missing_receipt_is_noop(self, committed_session: AsyncSession) -> None:
        # Deleted between enqueue and pickup. Should not raise.
        categorise_receipt.delay(str(uuid4()))


class TestPayloadCoercion:
    async def test_missing_merchant_uses_unknown_placeholder(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # Parser gave up on merchant. Task should still create an
        # expense (placeholder name) so the row stays visible to the
        # user; they can edit the merchant later.
        receipt = Receipt(
            user_id=user.id,
            storage_key=f"tests/{uuid4()}.jpg",
            mime_type="image/jpeg",
            file_size_bytes=64,
            status=ReceiptStatus.PARSED,
            parsed_payload={
                "merchant": None,
                "total": None,
                "transaction_date": None,
                "line_items": [],
            },
        )
        committed_session.add(receipt)
        await committed_session.commit()
        await committed_session.refresh(receipt)

        categorise_receipt.delay(str(receipt.id))

        expense = (
            await committed_session.execute(select(Expense).where(Expense.receipt_id == receipt.id))
        ).scalar_one()
        assert expense.merchant_name == "Unknown"
        assert expense.amount == Decimal("0.00")
        assert expense.category == ExpenseCategory.OTHER

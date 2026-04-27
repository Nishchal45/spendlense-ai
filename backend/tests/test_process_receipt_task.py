"""Integration tests for the OCR pipeline task.

These exercise the full path: synthesise a receipt image with PIL,
upload it to MinIO, run the Celery task in eager mode, assert the
``receipts`` row landed in ``parsed`` with a populated ``parsed_payload``.

The pipeline task opens its own DB session inside a worker thread —
the project-wide transactional ``db_session`` fixture is the wrong
shape for that: the worker thread can't see uncommitted writes from
the test's outer transaction. So this module uses ``committed_session``
which talks to a real engine and commits for real. Test isolation
comes from per-test UUIDs and explicit row cleanup, not rollback.

We synthesise images at runtime rather than checking in fixture
binaries — the fixture would drift when we touch image preprocessing,
and a 600px white-background JPEG with rendered text is easier to
reason about than a captured photograph.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from io import BytesIO
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.storage import delete_object, put_object
from app.core.storage_keys import build_receipt_key
from app.models.enums import OcrMethod, ReceiptStatus
from app.models.receipt import Receipt
from app.models.user import User
from app.services.user_service import create_user
from app.tasks.process_receipt import process_receipt


def _render_receipt_jpeg(lines: list[str]) -> bytes:
    """Draw ``lines`` onto a white canvas and JPEG-encode."""
    width, line_height, padding = 600, 32, 20
    height = padding * 2 + line_height * len(lines)
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # PIL's ``load_default`` returns a tiny bitmap font; readable enough
    # for Tesseract on the rendered canvas without bundling a TTF.
    font = ImageFont.load_default()
    for index, line in enumerate(lines):
        draw.text((padding, padding + index * line_height), line, fill="black", font=font)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest_asyncio.fixture
async def committed_session() -> AsyncIterator[AsyncSession]:
    """Real-commit session for tests where a worker thread reads the DB.

    The Celery task opens its own connection inside a worker thread,
    so it can't see writes that live inside the per-test transaction
    used elsewhere in the suite. We use a real engine here, commit
    for real, and clean up explicitly at teardown by deleting the
    rows we created.
    """
    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created_user_ids: list[UUID] = []
    session = factory()
    session.info["created_user_ids"] = created_user_ids
    try:
        yield session
    finally:
        await session.close()
        # ``ON DELETE CASCADE`` on receipts.user_id wipes children too.
        if created_user_ids:
            async with factory() as cleanup:
                await cleanup.execute(delete(User).where(User.id.in_(created_user_ids)))
                await cleanup.commit()
        await engine.dispose()


async def _create_user(session: AsyncSession, email: str) -> User:
    user = await create_user(session, email=email, password="hunter2hunter2")
    session.info["created_user_ids"].append(user.id)
    return user


async def _seed_receipt(
    session: AsyncSession, *, user_id: UUID, body: bytes, mime_type: str = "image/jpeg"
) -> Receipt:
    """Put bytes to MinIO and seed the matching ``receipts`` row.

    Mirrors what ``receipt_service.create_receipt`` does, minus the
    enqueue — we want to control task invocation explicitly.
    """
    key = build_receipt_key(
        user_id=user_id,
        mime_type=mime_type,
        secret="test-secret-must-be-at-least-32-characters-long",
    )
    await put_object(key=key, body=body, mime_type=mime_type)
    receipt = Receipt(
        user_id=user_id,
        storage_key=key,
        mime_type=mime_type,
        file_size_bytes=len(body),
        status=ReceiptStatus.UPLOADED,
    )
    session.add(receipt)
    await session.commit()
    await session.refresh(receipt)
    return receipt


@pytest.fixture
async def user(committed_session: AsyncSession) -> User:
    return await _create_user(committed_session, f"ocr-{uuid4()}@example.com")


def _render_receipt_pdf(lines: list[str]) -> bytes:
    """Render lines on a single page and save as PDF."""
    width, height = 600, 400
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    for index, line in enumerate(lines):
        draw.text((20, 20 + index * 32), line, fill="black", font=font)
    buf = BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


class TestProcessReceiptHappyPath:
    async def test_jpeg_receipt_lands_parsed(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # Synthetic receipt with crisp text Tesseract can read.
        body = _render_receipt_jpeg(
            [
                "Blue Bottle Coffee",
                "315 Linden St",
                "Date: 2026-04-25",
                "Cappuccino  4.75",
                "TOTAL       4.75",
            ]
        )
        receipt = await _seed_receipt(committed_session, user_id=user.id, body=body)

        try:
            # Eager mode runs the task body synchronously in-process.
            process_receipt.delay(str(receipt.id))

            await committed_session.refresh(receipt)
            assert receipt.status == ReceiptStatus.PARSED
            assert receipt.ocr_method == OcrMethod.TESSERACT
            assert receipt.raw_text is not None
            # PIL's ``load_default`` font is a tiny bitmap; Tesseract
            # garbles the long words but the structural fields (the
            # date, the leading merchant line, the total) survive.
            assert "Linden" in receipt.raw_text or "linden" in receipt.raw_text.lower()
            assert receipt.parsed_payload is not None
            payload = receipt.parsed_payload
            assert payload.get("merchant") is not None
            assert payload.get("transaction_date") == "2026-04-25"
            assert receipt.ocr_confidence is not None
        finally:
            await delete_object(key=receipt.storage_key)

    async def test_pdf_receipt_lands_parsed(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # PR #D added PDF rasterisation. A renderable single-page PDF
        # should travel the same path as a JPEG: rasterise → Tesseract
        # → parse → ``parsed`` state.
        body = _render_receipt_pdf(
            [
                "Email Receipt",
                "Date: 2026-04-25",
                "TOTAL  19.99",
            ]
        )
        receipt = await _seed_receipt(
            committed_session, user_id=user.id, body=body, mime_type="application/pdf"
        )

        try:
            process_receipt.delay(str(receipt.id))
            await committed_session.refresh(receipt)
            assert receipt.status == ReceiptStatus.PARSED
            assert receipt.parsed_payload is not None
            assert receipt.parsed_payload.get("transaction_date") == "2026-04-25"
        finally:
            await delete_object(key=receipt.storage_key)


class TestProcessReceiptFailureModes:
    async def test_malformed_pdf_marked_failed(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # PR #D added rasterisation via poppler-utils, but garbage
        # bytes that claim to be PDF still can't be rendered. The
        # task surfaces that as a domain failure (no retry) rather
        # than crashing the worker.
        body = b"%PDF-1.4\nfake pdf body\n%%EOF\n"
        receipt = await _seed_receipt(
            committed_session, user_id=user.id, body=body, mime_type="application/pdf"
        )

        try:
            process_receipt.delay(str(receipt.id))
            await committed_session.refresh(receipt)
            assert receipt.status == ReceiptStatus.FAILED
            assert receipt.error_message is not None
            assert "rasteris" in receipt.error_message.lower()
        finally:
            await delete_object(key=receipt.storage_key)

    async def test_garbage_bytes_marked_failed(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # Bytes that *claim* to be JPEG (magic prefix) but aren't a
        # decodable image. The pipeline should mark failed, not retry
        # forever — bad bytes don't get better with retries.
        body = b"\xff\xd8\xff" + b"this is not actually a jpeg" * 4
        receipt = await _seed_receipt(committed_session, user_id=user.id, body=body)

        try:
            process_receipt.delay(str(receipt.id))
            await committed_session.refresh(receipt)
            assert receipt.status == ReceiptStatus.FAILED
            assert receipt.error_message is not None
        finally:
            await delete_object(key=receipt.storage_key)

    async def test_missing_receipt_id_is_noop(self, committed_session: AsyncSession) -> None:
        # Row deleted between enqueue and pickup. Should not raise.
        process_receipt.delay(str(uuid4()))
        # No assertion needed — absence of an exception is the contract.


class TestStateTransitions:
    async def test_processing_state_visible_during_run(
        self, committed_session: AsyncSession, user: User
    ) -> None:
        # We can't easily observe the intermediate ``processing`` state
        # under eager mode (the task runs to completion before
        # ``delay`` returns) — but the row should leave ``uploaded``.
        body = _render_receipt_jpeg(["Test Merchant", "TOTAL 1.00"])
        receipt = await _seed_receipt(committed_session, user_id=user.id, body=body)

        try:
            assert receipt.status == ReceiptStatus.UPLOADED
            process_receipt.delay(str(receipt.id))
            await committed_session.refresh(receipt)
            assert receipt.status in {ReceiptStatus.PARSED, ReceiptStatus.FAILED}
        finally:
            await delete_object(key=receipt.storage_key)

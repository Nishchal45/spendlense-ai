"""OCR + parse pipeline for a single receipt.

Pulls the blob from object storage, runs Tesseract, parses the text
into structured fields, and writes the result back to the
``receipts`` row. State machine:

    uploaded ─▶ processing ─▶ parsed
                          └─▶ failed

PR #C extends this with categorisation (parsed → categorised) and
PR #D adds the GPT-4V fallback for low-confidence OCR results.

Why ``asyncio.run`` inside a sync task: Celery workers run sync by
default. Our DB and S3 clients are async. The cleanest bridge is a
fresh event loop per task — avoids dragging in ``celery-aio-pool``
or duplicating SQLAlchemy bindings for a sync session. Each task
owns its loop; nothing leaks between tasks.

Retries: any unhandled exception bubbles into Celery's ``self.retry``
with exponential backoff (2/4/8 seconds), bounded at 3 attempts.
After the last failure the row is marked ``failed`` with the
exception message; the Phase 6 nightly sweeper (or a ``POST
/receipts/{id}/retry`` from PR #D) is the recovery path.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.storage import s3_client
from app.models.enums import OcrMethod, ReceiptStatus
from app.models.receipt import Receipt
from app.services.image_preprocess import for_ocr, load_image
from app.services.ocr import run_tesseract
from app.services.pdf_rasterise import PdfRasterError, rasterise_first_page
from app.services.receipt_parser import ParsedReceipt, parse_receipt_text
from app.services.vision_ocr import extract_with_vision
from app.tasks.celery_app import celery_app

log = structlog.get_logger()

_ERROR_MESSAGE_MAX_LEN = 500
_PDF_MIME = "application/pdf"


@celery_app.task(name="spendlens.process_receipt", bind=True, max_retries=3)
def process_receipt(self: Any, receipt_id: str) -> None:
    """Entry point Celery dispatches to. Runs the async pipeline body."""
    try:
        _run_sync(_run(receipt_id))
    except Exception as exc:  # noqa: BLE001 — surface every error to Celery retry
        # ``self.retry`` raises ``Retry``, which Celery catches and re-queues.
        # Backoff doubles each attempt: 2s → 4s → 8s, then permanent fail.
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc


def _run_sync(coro: Coroutine[Any, Any, None]) -> None:
    """Run an async coroutine to completion from a sync context.

    Production: a Celery worker is sync; ``asyncio.run`` opens a fresh
    event loop and we're done. Tests: pytest-asyncio already owns a
    loop in the calling thread, so ``asyncio.run`` would raise. We
    detect a running loop and farm the work out to a worker thread
    that owns its own loop. The thread is invisible in production —
    no running loop means we never take that branch.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop. Production worker / direct sync caller.
        asyncio.run(coro)
        return

    # Already inside a loop (eager-mode pytest). Run on a thread that
    # owns its own loop so we never re-enter the caller's.
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, coro).result()


@asynccontextmanager
async def _task_session() -> AsyncIterator[AsyncSession]:
    """Per-invocation DB session bound to the task's own event loop.

    Each ``asyncio.run`` call inside ``_run_sync`` opens a fresh
    event loop. asyncpg's connection objects are bound to whichever
    loop they were created on, so we *cannot* reuse the
    module-global engine from ``app.core.database`` — a connection
    born in a previous task's (now-closed) loop fails on the next
    use with ``Event loop is closed``. Building a single-shot engine
    per task and disposing it before the loop closes keeps everything
    inside one well-defined lifetime.
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
    settings = get_settings()

    async with _task_session() as session:
        receipt = (
            await session.execute(select(Receipt).where(Receipt.id == receipt_id))
        ).scalar_one_or_none()
        if receipt is None:
            # Row was deleted between enqueue and pickup. Idempotent
            # no-op — nothing to do, no retry.
            log.warning("receipts.process.missing", receipt_id=receipt_id_str)
            return

        # Mark in-progress so a polling client sees motion.
        receipt.status = ReceiptStatus.PROCESSING
        await session.commit()

        try:
            await _ocr_and_parse(receipt, settings.s3_bucket)
            await session.commit()
            _enqueue_categorisation(receipt_id_str)
            log.info(
                "receipts.process.parsed",
                receipt_id=receipt_id_str,
                ocr_confidence=float(receipt.ocr_confidence or 0),
            )
        except _PipelineError as exc:
            # Domain failure (bad MIME, decode error). Mark the row
            # ``failed`` and *don't* re-raise — retries won't fix
            # the bytes on disk.
            receipt.status = ReceiptStatus.FAILED
            receipt.error_message = str(exc)[:_ERROR_MESSAGE_MAX_LEN]
            await session.commit()
            log.warning(
                "receipts.process.failed",
                receipt_id=receipt_id_str,
                reason=str(exc),
            )
        except Exception as exc:
            # Unknown / transient. Persist the failure marker so the
            # row reflects the latest attempt, then re-raise so
            # Celery's retry kicks in.
            receipt.status = ReceiptStatus.FAILED
            receipt.error_message = str(exc)[:_ERROR_MESSAGE_MAX_LEN]
            await session.commit()
            raise


class _PipelineError(Exception):
    """Domain error inside the OCR pipeline that shouldn't trigger retry."""


def _enqueue_categorisation(receipt_id: str) -> None:
    """Hand off to the categorise-and-create-expense task.

    Short-circuits in the test environment for the same reason
    ``receipt_service._enqueue_processing`` does — the integration
    suite exercises CRUD shouldn't pay for a downstream Celery dispatch
    on every parse. Tests that *want* categorisation invoke
    ``categorise_receipt`` directly.

    Imports are local to dodge a real circular import: the
    categorisation task module imports ``celery_app`` (and is
    discovered by it via ``conf.imports``). Importing it at module top
    here is fine in practice but makes the dependency graph noisier
    than it needs to be.
    """
    if get_settings().environment == "test":
        return
    from app.tasks.categorise_receipt import categorise_receipt

    categorise_receipt.delay(receipt_id)


async def _ocr_and_parse(receipt: Receipt, bucket: str) -> None:
    """Mutate ``receipt`` in-place with parsed payload + status.

    Pipeline branches:

    1. PDF input → rasterise the first page to an RGB image via
       ``poppler-utils``, then continue down the same path as native
       images.
    2. Image input → decode bytes (``pillow-heif`` registered, so
       HEIC works).
    3. Run Tesseract for text + mean per-word confidence.
    4. If confidence is below ``settings.ocr_confidence_threshold``
       *and* an OpenAI key is configured, hand the original image to
       GPT-4V and use its structured-extract result instead. Records
       ``ocr_method=GPT4V`` so analytics can see how often the
       fallback fires.
    5. Else, run the regex parser on the Tesseract text. Records
       ``ocr_method=TESSERACT``.

    The session is committed by the caller — this function only
    mutates fields so failure-handling cleanup stays in one place.
    """
    body = await _download_blob(bucket, receipt.storage_key)

    image_bytes_for_vision = body
    image_mime_for_vision = receipt.mime_type

    if receipt.mime_type == _PDF_MIME:
        try:
            img = rasterise_first_page(body)
        except PdfRasterError as exc:
            raise _PipelineError(f"Could not rasterise PDF: {exc}") from exc
        # Vision call needs the rasterised image, not the PDF bytes —
        # OpenAI's image input doesn't accept PDFs directly.
        image_bytes_for_vision = _encode_jpeg(img)
        image_mime_for_vision = "image/jpeg"
    else:
        try:
            img = load_image(body)
        except Exception as exc:
            raise _PipelineError(f"Could not decode image: {exc}") from exc

    ocr_result = run_tesseract(for_ocr(img))
    receipt.raw_text = ocr_result.text
    receipt.ocr_confidence = round(ocr_result.mean_confidence, 2)  # type: ignore[assignment]

    settings = get_settings()
    parsed: ParsedReceipt
    method: OcrMethod
    if ocr_result.mean_confidence < settings.ocr_confidence_threshold and settings.openai_api_key:
        vision_result = await extract_with_vision(
            image_bytes=image_bytes_for_vision,
            image_mime=image_mime_for_vision,
            model=settings.openai_model_vision,
        )
        if vision_result is not None:
            parsed = vision_result
            method = OcrMethod.GPT4V
            log.info(
                "receipts.process.vision_used",
                receipt_id=str(receipt.id),
                tesseract_confidence=ocr_result.mean_confidence,
            )
        else:
            # Vision fell over (no key, network, malformed JSON). Take
            # the Tesseract reading rather than fail outright.
            parsed = parse_receipt_text(ocr_result.text)
            method = OcrMethod.TESSERACT
    else:
        parsed = parse_receipt_text(ocr_result.text)
        method = OcrMethod.TESSERACT

    receipt.parsed_payload = parsed.to_jsonable()
    receipt.ocr_method = method
    receipt.status = ReceiptStatus.PARSED
    receipt.error_message = None


def _encode_jpeg(img: Any) -> bytes:
    """Re-encode a PIL image as JPEG bytes for the vision API."""
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


async def _download_blob(bucket: str, key: str) -> bytes:
    async with s3_client() as client:
        response = await client.get_object(Bucket=bucket, Key=key)
        body: bytes = await response["Body"].read()
        return body

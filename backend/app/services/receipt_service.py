"""Receipt persistence + object-storage orchestration.

The upload flow is a two-step transaction across two systems:

1. Put the blob to S3 under a freshly-minted opaque key.
2. Insert the ``receipts`` row pointing at that key.

If step 2 fails we **cannot** rely on a DB rollback to also unlink the
blob — S3 has no transaction scope. We explicitly cleanup the blob on
DB-write failure so a partial upload doesn't leak storage.

MIME is validated by *sniffing the first bytes*, not by trusting the
``Content-Type`` the client sent. An attacker can always send
``image/jpeg`` for an executable; the magic-byte check is the real
gate. Unsupported types are rejected before any bytes hit the object
store.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.storage import delete_object, presign_get_url, put_object
from app.core.storage_keys import build_receipt_key
from app.models.enums import ReceiptStatus
from app.models.receipt import Receipt
from app.tasks.process_receipt import process_receipt

log = structlog.get_logger()


class UnsupportedMediaTypeError(Exception):
    """Raised when an upload's bytes don't match an allowed image/PDF type."""


class PayloadTooLargeError(Exception):
    """Raised when an upload exceeds ``MAX_UPLOAD_BYTES``."""


class ReceiptNotFoundError(Exception):
    """Raised when a receipt doesn't exist or isn't owned by the caller."""


# 10 MiB is generous for a receipt photo (modern phones produce 2-5 MiB JPEGs
# and HEIC is smaller). The cap exists to keep a single upload from pinning
# an API worker on the wire or filling the bucket from one malicious client.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# Magic-byte prefixes for every MIME type we accept.
# Ref: JPEG (JFIF/EXIF) https://en.wikipedia.org/wiki/JPEG_File_Interchange_Format
#      PNG https://www.w3.org/TR/PNG/#5PNG-file-signature
#      WEBP/HEIC are ISO BMFF containers — the brand is at bytes 8..12.
#      PDF starts with "%PDF-".
_MAGIC_RULES: list[tuple[bytes, str]] = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"%PDF-", "application/pdf"),
]

# ISO BMFF container header layout: first 4 bytes are the box length, the
# next 4 are the box type (``ftyp``), and bytes 8..12 are the major brand.
_ISO_BMFF_HEADER_LEN = 12


def _sniff_mime(data: bytes) -> str | None:
    """Return the detected MIME type or ``None`` if unsupported.

    We check magic bytes over the first 16 bytes. WEBP/HEIC sniffing
    is deliberately explicit because both are ISO BMFF containers and
    need a look at the ``ftyp`` brand.
    """
    for prefix, mime in _MAGIC_RULES:
        if data.startswith(prefix):
            return mime

    # ISO BMFF: bytes 4..8 are the box type (always "ftyp" for the
    # container types we care about), bytes 8..12 are the major brand.
    if len(data) >= _ISO_BMFF_HEADER_LEN and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in {b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1"}:
            return "image/heic"
        if brand in {b"WEBP"}:
            return "image/webp"

    return None


async def create_receipt(
    session: AsyncSession,
    *,
    user_id: UUID,
    body: bytes,
) -> Receipt:
    """Validate, upload, and persist a new receipt.

    Raises:
        PayloadTooLargeError: if ``body`` exceeds the size cap.
        UnsupportedMediaTypeError: if the bytes don't sniff to an
            allowed type.
    """
    if len(body) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(str(len(body)))
    if not body:
        raise UnsupportedMediaTypeError("empty upload")

    mime_type = _sniff_mime(body[:32])
    if mime_type is None:
        raise UnsupportedMediaTypeError("unknown magic bytes")

    settings = get_settings()
    key = build_receipt_key(user_id=user_id, mime_type=mime_type, secret=settings.jwt_secret)

    # S3 first. If the DB insert then fails we clean up the blob; the
    # other order (DB first) would leave a row pointing at a missing
    # object when S3 is the layer that flaked.
    await put_object(key=key, body=body, mime_type=mime_type)

    receipt = Receipt(
        user_id=user_id,
        storage_key=key,
        mime_type=mime_type,
        file_size_bytes=len(body),
        status=ReceiptStatus.UPLOADED,
    )
    session.add(receipt)
    try:
        await session.flush()
        await session.commit()
    except Exception:
        # Object survives the crashed transaction — wipe it so we don't
        # leak storage. Logged, not swallowed, so ops can spot a trend.
        log.warning("receipts.rollback_orphan_blob", storage_key=key)
        await delete_object(key=key)
        raise
    await session.refresh(receipt)

    _enqueue_processing(receipt.id)
    return receipt


def _enqueue_processing(receipt_id: UUID) -> None:
    """Hand the new receipt off to the OCR worker.

    Short-circuits in the test environment so the integration suite
    that exercises CRUD doesn't drag Tesseract into every upload —
    the dedicated pipeline tests call ``process_receipt`` directly to
    cover the real path. Production / dev / staging always enqueue.
    """
    if get_settings().environment == "test":
        return
    process_receipt.delay(str(receipt_id))


async def get_receipt(
    session: AsyncSession,
    *,
    user_id: UUID,
    receipt_id: UUID,
) -> Receipt:
    """Fetch a receipt the user owns or raise ``ReceiptNotFoundError``.

    Ownership is enforced in the query, not a Python check after load.
    A stranger asking for another user's receipt id sees 404, not 403,
    so existence can't be probed.
    """
    stmt = select(Receipt).where(and_(Receipt.id == receipt_id, Receipt.user_id == user_id))
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        raise ReceiptNotFoundError(str(receipt_id))
    return result


async def list_receipts(
    session: AsyncSession,
    *,
    user_id: UUID,
    limit: int = 50,
) -> list[Receipt]:
    """Return the user's most recent receipts, newest first."""
    stmt = (
        select(Receipt)
        .where(Receipt.user_id == user_id)
        .order_by(Receipt.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return list((await session.execute(stmt)).scalars().all())


async def delete_receipt(
    session: AsyncSession,
    *,
    user_id: UUID,
    receipt_id: UUID,
) -> None:
    """Delete a receipt row and its blob. Idempotent on the blob side."""
    receipt = await get_receipt(session, user_id=user_id, receipt_id=receipt_id)
    key = receipt.storage_key

    await session.delete(receipt)
    await session.commit()

    # Best-effort blob cleanup. The row is already gone, so a transient
    # S3 failure would strand the object — we log, don't re-raise, so
    # the API still reports success. A nightly sweeper (Phase 6+) can
    # reconcile orphaned keys by listing the bucket and diffing with
    # the table.
    try:
        await delete_object(key=key)
    except Exception:  # noqa: BLE001 - intentional broad catch
        log.warning("receipts.blob_delete_failed", storage_key=key, receipt_id=str(receipt_id))


async def build_download_url(receipt: Receipt) -> str:
    """Return a short-lived signed URL for the receipt's blob."""
    return await presign_get_url(key=receipt.storage_key)

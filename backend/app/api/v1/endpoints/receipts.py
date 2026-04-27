"""Receipt upload + download surface.

Routes delegate to ``receipt_service`` and translate domain exceptions
to HTTP status codes. The download path is indirect on purpose: clients
call ``GET /receipts/{id}/url`` and receive a short-lived signed URL,
they never see the storage key. That keeps the object-key scheme
internal — we can rotate prefixes, change buckets, or swap backends
without breaking clients.

Uploads stream in as ``multipart/form-data`` so large payloads don't
get base64-bloated. The handler reads the full body into memory — 10
MiB ceiling (enforced in ``receipt_service``) keeps this safe for an
API worker and matches real phone photo sizes.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel

from app.api.v1.deps import CurrentUser, SessionDep
from app.schemas.receipt import ReceiptList, ReceiptOut, ReceiptStatusOut
from app.services.receipt_service import (
    PayloadTooLargeError,
    ReceiptNotFoundError,
    ReceiptNotRetryableError,
    UnsupportedMediaTypeError,
    build_download_url,
    create_receipt,
    delete_receipt,
    get_receipt,
    list_receipts,
    retry_receipt,
)

router = APIRouter(prefix="/receipts", tags=["receipts"])
log = structlog.get_logger()


class ReceiptDownloadUrl(BaseModel):
    """Envelope for ``GET /receipts/{id}/url``.

    A wrapper object (rather than a bare string) keeps room for future
    fields — expiry timestamp, content-type hint — without a breaking
    API change.
    """

    url: str


def _not_found() -> HTTPException:
    # 404 not 403: existence of another user's receipts is not probable
    # through this API surface.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receipt not found")


@router.post("", response_model=ReceiptOut, status_code=status.HTTP_201_CREATED)
async def upload(
    current_user: CurrentUser,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
) -> ReceiptOut:
    """Accept a multipart upload and persist a new receipt row."""
    body = await file.read()
    try:
        receipt = await create_receipt(session, user_id=current_user.id, body=body)
    except PayloadTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Upload exceeds size limit",
        ) from exc
    except UnsupportedMediaTypeError as exc:
        # 415 covers both "empty upload" and "unknown magic bytes" — the
        # service distinguishes them in the exception message for
        # structured logs, but the client just needs "your file type
        # isn't allowed."
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file type",
        ) from exc

    log.info(
        "receipts.uploaded",
        user_id=str(current_user.id),
        receipt_id=str(receipt.id),
        mime_type=receipt.mime_type,
        size=receipt.file_size_bytes,
    )
    return ReceiptOut.model_validate(receipt)


@router.get("", response_model=ReceiptList)
async def list_(
    current_user: CurrentUser,
    session: SessionDep,
) -> ReceiptList:
    receipts = await list_receipts(session, user_id=current_user.id)
    return ReceiptList(items=[ReceiptOut.model_validate(r) for r in receipts])


@router.get("/{receipt_id}", response_model=ReceiptOut)
async def get(
    receipt_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> ReceiptOut:
    try:
        receipt = await get_receipt(session, user_id=current_user.id, receipt_id=receipt_id)
    except ReceiptNotFoundError as exc:
        raise _not_found() from exc
    return ReceiptOut.model_validate(receipt)


@router.get("/{receipt_id}/status", response_model=ReceiptStatusOut)
async def get_status(
    receipt_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> ReceiptStatusOut:
    """Polling-friendly snapshot of pipeline progress.

    Clients hitting this on an interval see the receipt walk through
    ``uploaded → processing → parsed → categorised`` without paying
    for the full receipt projection. ``error_message`` and
    ``parsed_payload`` are exposed so the UI can render "we got the
    merchant and total — confirm or fix?" without a second fetch.
    """
    try:
        receipt = await get_receipt(session, user_id=current_user.id, receipt_id=receipt_id)
    except ReceiptNotFoundError as exc:
        raise _not_found() from exc
    return ReceiptStatusOut.model_validate(receipt)


@router.post("/{receipt_id}/retry", response_model=ReceiptOut, status_code=status.HTTP_202_ACCEPTED)
async def retry(
    receipt_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> ReceiptOut:
    """Re-enqueue a receipt that previously landed in ``failed``.

    202 Accepted (not 200) because the actual reprocessing happens on
    the worker — the response means "your retry is queued", not "your
    receipt is parsed". Returns the reset receipt so the client can
    flip its UI back to "processing" without an extra GET.

    409 Conflict if the row isn't currently in ``failed`` — retrying
    a row that's still in flight, or one that already produced an
    expense, is a logic bug on the client side and we surface that
    rather than silently re-run the pipeline.
    """
    try:
        receipt = await retry_receipt(session, user_id=current_user.id, receipt_id=receipt_id)
    except ReceiptNotFoundError as exc:
        raise _not_found() from exc
    except ReceiptNotRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Receipt is not in a retryable state (current: {exc})",
        ) from exc

    log.info(
        "receipts.retried",
        user_id=str(current_user.id),
        receipt_id=str(receipt_id),
    )
    return ReceiptOut.model_validate(receipt)


@router.get("/{receipt_id}/url", response_model=ReceiptDownloadUrl)
async def download_url(
    receipt_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> ReceiptDownloadUrl:
    """Issue a short-lived signed URL for the receipt's blob.

    The TTL lives in ``app.core.storage.PRESIGN_GET_TTL_SECONDS`` —
    long enough to survive a slow mobile download, short enough that a
    leaked link has a small blast radius. Each call mints a fresh URL,
    so refresh-on-expiry is a client-side retry, not a server change.
    """
    try:
        receipt = await get_receipt(session, user_id=current_user.id, receipt_id=receipt_id)
    except ReceiptNotFoundError as exc:
        raise _not_found() from exc
    url = await build_download_url(receipt)
    return ReceiptDownloadUrl(url=url)


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    receipt_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> Response:
    try:
        await delete_receipt(session, user_id=current_user.id, receipt_id=receipt_id)
    except ReceiptNotFoundError as exc:
        raise _not_found() from exc

    log.info(
        "receipts.deleted",
        user_id=str(current_user.id),
        receipt_id=str(receipt_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

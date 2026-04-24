"""Wire contracts for the receipts upload surface.

The request side is a multipart form, so it doesn't have a Pydantic
input model — the route signature accepts ``UploadFile`` directly.
These schemas cover the response shapes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import OcrMethod, ReceiptStatus


class ReceiptOut(BaseModel):
    """Public projection of a ``Receipt`` row.

    The storage key is intentionally *not* exposed to clients. Downloads
    flow through a short-lived signed URL issued by ``GET /receipts/{id}/url``
    (Phase 4 PR #C) so the object path can rotate without breaking links.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    mime_type: str
    file_size_bytes: int
    status: ReceiptStatus
    ocr_method: OcrMethod | None
    ocr_confidence: Decimal | None
    created_at: datetime
    updated_at: datetime


class ReceiptList(BaseModel):
    """Envelope for ``GET /receipts``. Pagination lands with the queue
    wiring in Phase 5 — a freshly-uploaded stack is short enough that a
    hard cap (``MAX_PAGE_SIZE``) suffices for now."""

    items: list[ReceiptOut]

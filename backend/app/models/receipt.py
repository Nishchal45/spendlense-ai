from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import OcrMethod, ReceiptStatus, pg_enum

if TYPE_CHECKING:
    from app.models.expense import Expense
    from app.models.user import User


class Receipt(Base, TimestampMixin):
    __tablename__ = "receipts"
    __table_args__ = (
        Index("ix_receipts_user_created", "user_id", "created_at"),
        Index("ix_receipts_status", "status"),
        # Per-user dedup for inbound-email ingestion (Phase 5.5).
        # Partial unique index — only enforces uniqueness when
        # ``external_message_id`` is set, so manual uploads (which
        # leave it NULL) don't collide.
        Index(
            "uq_receipts_user_external_message_id",
            "user_id",
            "external_message_id",
            unique=True,
            postgresql_where="external_message_id IS NOT NULL",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[ReceiptStatus] = mapped_column(
        pg_enum(ReceiptStatus, name="receipt_status"),
        nullable=False,
        default=ReceiptStatus.UPLOADED,
    )
    ocr_method: Mapped[OcrMethod | None] = mapped_column(
        pg_enum(OcrMethod, name="ocr_method"), nullable=True
    )
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Provider-supplied message id for receipts ingested via the
    # inbound-email webhook (Phase 5.5). NULL for receipts uploaded
    # directly. The partial unique index in ``__table_args__`` gives
    # us per-user dedup against the same email being delivered twice.
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship(back_populates="receipts")
    expense: Mapped["Expense | None"] = relationship(back_populates="receipt", uselist=False)

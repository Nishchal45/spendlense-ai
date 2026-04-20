from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import ExpenseCategory

if TYPE_CHECKING:
    from app.models.user import User


class CategoryCorrection(Base):
    __tablename__ = "category_corrections"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_name", name="uq_correction_user_merchant"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[ExpenseCategory] = mapped_column(
        SAEnum(ExpenseCategory, name="expense_category"), nullable=False
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="corrections")

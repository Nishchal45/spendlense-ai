from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Date, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import ExpenseCategory, ExpenseSource, pg_enum

if TYPE_CHECKING:
    from app.models.line_item import LineItem
    from app.models.receipt import Receipt
    from app.models.user import User


class Expense(Base, TimestampMixin):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_user_date", "user_id", "expense_date"),
        Index("ix_expenses_user_category", "user_id", "category"),
        Index("ix_expenses_merchant_name", "merchant_name"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    category: Mapped[ExpenseCategory] = mapped_column(
        pg_enum(ExpenseCategory, name="expense_category"), nullable=False
    )
    expense_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    receipt_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("receipts.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    source: Mapped[ExpenseSource] = mapped_column(
        pg_enum(ExpenseSource, name="expense_source"),
        nullable=False,
        default=ExpenseSource.MANUAL,
    )

    user: Mapped["User"] = relationship(back_populates="expenses")
    receipt: Mapped["Receipt | None"] = relationship(back_populates="expense")
    line_items: Mapped[list["LineItem"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan"
    )

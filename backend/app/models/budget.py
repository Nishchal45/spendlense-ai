from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import BudgetPeriod, ExpenseCategory

if TYPE_CHECKING:
    from app.models.user import User


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "period", name="uq_budget_per_category_period"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    category: Mapped[ExpenseCategory] = mapped_column(
        SAEnum(ExpenseCategory, name="expense_category"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    period: Mapped[BudgetPeriod] = mapped_column(
        SAEnum(BudgetPeriod, name="budget_period"),
        nullable=False,
        default=BudgetPeriod.MONTHLY,
    )
    alert_threshold_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=80)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    user: Mapped["User"] = relationship(back_populates="budgets")

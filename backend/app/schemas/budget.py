"""Wire contracts for the budgets surface."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import BudgetPeriod, ExpenseCategory

# Cap aligns with the ``Numeric(12, 2)`` column on ``budgets.amount``.
# Twelve digits before the decimal would still leave room for absurd
# numbers; this is the realistic upper bound on a personal budget.
MAX_BUDGET_AMOUNT = Decimal("9999999.99")


class BudgetCreate(BaseModel):
    """Payload for ``POST /budgets``."""

    category: ExpenseCategory
    amount: Decimal = Field(gt=Decimal("0"), le=MAX_BUDGET_AMOUNT)
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    alert_threshold_pct: int = Field(ge=1, le=200, default=80)
    active: bool = True


class BudgetUpdate(BaseModel):
    """Payload for ``PATCH /budgets/{id}``. Everything optional —
    Pydantic's ``exclude_unset=True`` round-trip lets the service
    ignore fields the client didn't touch."""

    category: ExpenseCategory | None = None
    amount: Decimal | None = Field(default=None, gt=Decimal("0"), le=MAX_BUDGET_AMOUNT)
    period: BudgetPeriod | None = None
    alert_threshold_pct: int | None = Field(default=None, ge=1, le=200)
    active: bool | None = None


class BudgetOut(BaseModel):
    """Public projection of a ``budgets`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    category: ExpenseCategory
    amount: Decimal
    period: BudgetPeriod
    alert_threshold_pct: int
    active: bool
    created_at: datetime
    updated_at: datetime


class BudgetList(BaseModel):
    """Envelope for ``GET /budgets``."""

    items: list[BudgetOut]


class BudgetStatusOut(BaseModel):
    """One row of ``GET /budgets/status``."""

    model_config = ConfigDict(from_attributes=True)

    budget_id: UUID
    category: ExpenseCategory
    period: BudgetPeriod
    amount: Decimal
    spent: Decimal
    remaining: Decimal
    ratio: float
    alert_threshold_pct: int
    alert_triggered: bool
    period_start: date
    period_end: date


class BudgetStatusReportOut(BaseModel):
    """Response for ``GET /budgets/status``."""

    model_config = ConfigDict(from_attributes=True)

    today: date
    items: list[BudgetStatusOut]

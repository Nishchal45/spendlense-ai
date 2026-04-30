"""Budget CRUD + spend-vs-budget status.

The ``budgets`` table from Phase 1 already carries the columns we
need (category, amount, period, ``alert_threshold_pct``, ``active``)
plus a unique constraint on ``(user_id, category, period)``. This
service exposes the CRUD surface and the read-side ``status`` query
that joins the user's budgets against current-month spend.

State worth flagging:

* **One budget per (user, category, period).** The unique
  constraint enforces it at the DB layer — duplicate POSTs raise
  ``BudgetAlreadyExistsError`` (409) rather than silently
  overwriting.
* **Inactive budgets are excluded from status.** ``active=False``
  is a soft-delete affordance: the user can pause a budget without
  losing the threshold history.
* **Status spend window is the current calendar month**, derived
  from today via the same :func:`month_bounds` helper the monthly
  breakdown uses. Other periods (weekly, yearly) ride the same
  pattern when ``BudgetPeriod`` grows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.enums import BudgetPeriod, ExpenseCategory
from app.models.expense import Expense
from app.services.insights_service import month_bounds


class BudgetNotFoundError(Exception):
    """Raised when a budget doesn't exist or isn't owned by the caller."""


class BudgetAlreadyExistsError(Exception):
    """Raised when a POST would violate ``uq_budget_per_category_period``."""


# ----- CRUD ---------------------------------------------------------------


async def create_budget(
    session: AsyncSession,
    *,
    user_id: UUID,
    payload: dict[str, Any],
) -> Budget:
    """Insert a budget. Raises ``BudgetAlreadyExistsError`` on the
    unique-constraint collision so the router can map it to 409.
    """
    budget = Budget(user_id=user_id, **payload)
    session.add(budget)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BudgetAlreadyExistsError(
            f"category={payload.get('category')} period={payload.get('period')}"
        ) from exc
    await session.refresh(budget)
    return budget


async def get_budget(
    session: AsyncSession,
    *,
    user_id: UUID,
    budget_id: UUID,
) -> Budget:
    """Return ``budget_id`` if owned by ``user_id``. 404-shaped error
    otherwise — we don't distinguish "doesn't exist" from "isn't
    yours" so existence can't be probed."""
    stmt = select(Budget).where(and_(Budget.id == budget_id, Budget.user_id == user_id))
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        raise BudgetNotFoundError(str(budget_id))
    return result


async def list_budgets(
    session: AsyncSession,
    *,
    user_id: UUID,
    include_inactive: bool = False,
) -> list[Budget]:
    """Return the user's budgets, sorted by category for stable UI
    rendering. ``include_inactive=False`` matches the dashboard
    default (paused budgets are out of sight by default)."""
    stmt = select(Budget).where(Budget.user_id == user_id)
    if not include_inactive:
        stmt = stmt.where(Budget.active.is_(True))
    stmt = stmt.order_by(Budget.category)
    return list((await session.execute(stmt)).scalars().all())


async def update_budget(
    session: AsyncSession,
    *,
    user_id: UUID,
    budget_id: UUID,
    patch: dict[str, Any],
) -> Budget:
    """Partial-update a budget. Same ``model_dump(exclude_unset=True)``
    contract as the expenses surface — unset fields don't appear and
    therefore don't touch the row."""
    budget = await get_budget(session, user_id=user_id, budget_id=budget_id)
    for field, value in patch.items():
        setattr(budget, field, value)

    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise BudgetAlreadyExistsError(
            f"category={patch.get('category')} period={patch.get('period')}"
        ) from exc
    await session.refresh(budget)
    return budget


async def delete_budget(
    session: AsyncSession,
    *,
    user_id: UUID,
    budget_id: UUID,
) -> None:
    """Hard-delete a budget. The ``active`` flag is the soft-delete
    path — explicit ``DELETE`` is for "I'm not tracking this category
    at all anymore"."""
    budget = await get_budget(session, user_id=user_id, budget_id=budget_id)
    await session.delete(budget)
    await session.commit()


# ----- status -------------------------------------------------------------


@dataclass(frozen=True)
class BudgetStatus:
    """One row of the budget-status response.

    ``ratio`` is ``spent / amount`` clamped at zero on the low end
    (no negative spend) but *not* on the high end — going over the
    cap should render as 110%, 150% etc. so the UI can colour it
    accordingly.

    ``alert_triggered`` reflects whether the user's chosen threshold
    has been crossed *for the current period*; the dashboard lights
    up the row when this is true.
    """

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


@dataclass(frozen=True)
class BudgetStatusReport:
    """Response for ``GET /budgets/status``.

    Returned in the same order as :func:`list_budgets` so a client
    can zip the two lists if it ever needs both views together.
    """

    today: date
    items: list[BudgetStatus]


async def budget_status(
    session: AsyncSession,
    *,
    user_id: UUID,
    today: date,
) -> BudgetStatusReport:
    """Spend-vs-budget for every active monthly budget the user has.

    Two queries:

    1. Pull the user's active budgets (small list, single round trip).
    2. Sum ``expenses.amount`` per category over the current period.

    We deliberately skip non-monthly budgets here — ``BudgetPeriod``
    only has ``MONTHLY`` today; the moment ``WEEKLY`` / ``YEARLY``
    ship we'll route each through its own period-bounds helper. A
    weekly budget on a monthly window would silently misreport
    progress, so the period filter is explicit.
    """
    budgets = await list_budgets(session, user_id=user_id, include_inactive=False)
    monthly = [b for b in budgets if b.period == BudgetPeriod.MONTHLY]
    if not monthly:
        # No active monthly budgets; nothing to status. Skip the
        # second query entirely.
        return BudgetStatusReport(today=today, items=[])

    period_start, period_end = month_bounds(today)
    categories = [b.category for b in monthly]

    spend_stmt = select(Expense.category, Expense.amount).where(
        Expense.user_id == user_id,
        Expense.expense_date >= period_start,
        Expense.expense_date < period_end,
        Expense.category.in_(categories),
    )
    rows = (await session.execute(spend_stmt)).all()
    spent_by_category: dict[ExpenseCategory, Decimal] = {}
    for row in rows:
        spent_by_category[row.category] = (
            spent_by_category.get(row.category, Decimal("0")) + row.amount
        )

    items: list[BudgetStatus] = []
    for budget in monthly:
        spent = spent_by_category.get(budget.category, Decimal("0"))
        # Clamp ratio at zero on the low end; allow >1.0 on the high
        # end so the UI can render "150% of budget" in red.
        ratio = float(spent / budget.amount) if budget.amount > 0 else 0.0
        ratio = max(ratio, 0.0)
        threshold_pct = budget.alert_threshold_pct
        items.append(
            BudgetStatus(
                budget_id=budget.id,
                category=budget.category,
                period=budget.period,
                amount=budget.amount,
                spent=spent,
                remaining=budget.amount - spent,
                ratio=ratio,
                alert_threshold_pct=threshold_pct,
                alert_triggered=ratio * 100 >= threshold_pct,
                period_start=period_start,
                period_end=period_end,
            )
        )

    return BudgetStatusReport(today=today, items=items)

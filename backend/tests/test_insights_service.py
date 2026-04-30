"""Unit tests for the insights service.

These exercise the SQL aggregations directly without touching the
HTTP layer. Each test seeds a tight fixture of expenses and asserts
that the aggregator picks the right rows and emits the right rollup
shape — the kind of thing that breaks when someone accidentally
changes ``>=`` to ``>`` on a date boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExpenseCategory, ExpenseSource
from app.models.expense import Expense
from app.models.user import User
from app.services.insights_service import (
    _months_back,
    category_trends,
    month_bounds,
    monthly_breakdown,
)
from app.services.user_service import create_user


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session, email=f"insights-{uuid4()}@example.com", password="hunter2hunter2"
    )


async def _add_expense(
    session: AsyncSession,
    *,
    user_id: UUID,
    amount: str,
    category: ExpenseCategory,
    on_date: date,
    merchant: str = "Test Merchant",
) -> None:
    session.add(
        Expense(
            user_id=user_id,
            merchant_name=merchant,
            amount=Decimal(amount),
            currency="USD",
            category=category,
            expense_date=on_date,
            source=ExpenseSource.MANUAL,
        )
    )
    await session.flush()


class TestMonthBounds:
    def test_first_of_month(self) -> None:
        start, end = month_bounds(date(2026, 4, 1))
        assert start == date(2026, 4, 1)
        assert end == date(2026, 5, 1)

    def test_mid_month_snaps_to_first(self) -> None:
        start, _ = month_bounds(date(2026, 4, 17))
        assert start == date(2026, 4, 1)

    def test_december_rolls_to_january(self) -> None:
        start, end = month_bounds(date(2026, 12, 15))
        assert start == date(2026, 12, 1)
        assert end == date(2027, 1, 1)


class TestMonthsBack:
    def test_zero_returns_current_month(self) -> None:
        assert _months_back(date(2026, 4, 1), 0) == date(2026, 4, 1)

    def test_one_month_back(self) -> None:
        assert _months_back(date(2026, 4, 1), 1) == date(2026, 3, 1)

    def test_crosses_year_boundary(self) -> None:
        assert _months_back(date(2026, 2, 1), 6) == date(2025, 8, 1)

    def test_full_year(self) -> None:
        assert _months_back(date(2026, 4, 1), 12) == date(2025, 4, 1)


class TestMonthlyBreakdown:
    async def test_groups_by_category_and_sorts_by_total_desc(
        self, db_session: AsyncSession, user: User
    ) -> None:
        # Two FOOD_DINING ($10), one GROCERIES ($50).
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="4.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 4, 5),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="6.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 4, 18),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="50.00",
            category=ExpenseCategory.GROCERIES,
            on_date=date(2026, 4, 22),
        )

        result = await monthly_breakdown(db_session, user_id=user.id, month=date(2026, 4, 1))
        assert result.month == date(2026, 4, 1)
        assert result.grand_total == Decimal("60.00")
        assert result.grand_count == 3
        # Sorted by total desc: groceries first.
        assert [item.category for item in result.items] == [
            ExpenseCategory.GROCERIES,
            ExpenseCategory.FOOD_DINING,
        ]
        assert result.items[0].count == 1
        assert result.items[1].count == 2
        assert result.items[1].average == Decimal("5.00")

    async def test_excludes_other_months(self, db_session: AsyncSession, user: User) -> None:
        # March 31 and May 1 must NOT show up in an April query —
        # canonical date-boundary off-by-one.
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="9.99",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 3, 31),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="9.99",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 5, 1),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="1.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 4, 30),
        )

        result = await monthly_breakdown(db_session, user_id=user.id, month=date(2026, 4, 17))
        assert result.grand_count == 1
        assert result.grand_total == Decimal("1.00")

    async def test_excludes_other_users(self, db_session: AsyncSession, user: User) -> None:
        # Another user's spend must not bleed into this user's
        # breakdown.
        stranger = await create_user(
            db_session, email=f"stranger-{uuid4()}@example.com", password="hunter2hunter2"
        )
        await _add_expense(
            db_session,
            user_id=stranger.id,
            amount="999.00",
            category=ExpenseCategory.SHOPPING,
            on_date=date(2026, 4, 12),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="3.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=date(2026, 4, 12),
        )

        result = await monthly_breakdown(db_session, user_id=user.id, month=date(2026, 4, 1))
        assert result.grand_total == Decimal("3.00")
        assert all(item.category != ExpenseCategory.SHOPPING for item in result.items)

    async def test_empty_month_returns_zero_grand_total(
        self, db_session: AsyncSession, user: User
    ) -> None:
        result = await monthly_breakdown(db_session, user_id=user.id, month=date(2026, 4, 1))
        assert result.items == []
        assert result.grand_total == Decimal("0")
        assert result.grand_count == 0


class TestCategoryTrends:
    async def test_dense_grid_with_zero_buckets(self, db_session: AsyncSession, user: User) -> None:
        # Spend in two non-adjacent months. The trend grid should
        # backfill zero for the gap so a chart library doesn't have
        # to know about missing months.
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="100.00",
            category=ExpenseCategory.GROCERIES,
            on_date=date(2026, 2, 10),
        )
        await _add_expense(
            db_session,
            user_id=user.id,
            amount="50.00",
            category=ExpenseCategory.GROCERIES,
            on_date=date(2026, 4, 10),
        )

        result = await category_trends(
            db_session, user_id=user.id, anchor=date(2026, 4, 30), months=4
        )
        # 4-month window ending April 2026 → Jan/Feb/Mar/Apr.
        assert result.months == [
            date(2026, 1, 1),
            date(2026, 2, 1),
            date(2026, 3, 1),
            date(2026, 4, 1),
        ]
        assert result.categories == [ExpenseCategory.GROCERIES]
        # Grid is dense — 4 months × 1 category = 4 buckets.
        assert len(result.buckets) == 4
        amounts = {bucket.month: bucket.total for bucket in result.buckets}
        assert amounts[date(2026, 1, 1)] == Decimal("0.00")
        assert amounts[date(2026, 2, 1)] == Decimal("100.00")
        assert amounts[date(2026, 3, 1)] == Decimal("0.00")
        assert amounts[date(2026, 4, 1)] == Decimal("50.00")

    async def test_clamps_window_to_max(self, db_session: AsyncSession, user: User) -> None:
        # Asking for 999 months should return at most MAX_TRENDS_MONTHS
        # buckets, not crash.
        from app.services.insights_service import MAX_TRENDS_MONTHS

        result = await category_trends(
            db_session, user_id=user.id, anchor=date(2026, 4, 30), months=999
        )
        assert len(result.months) == MAX_TRENDS_MONTHS

    async def test_no_spend_returns_empty_categories(
        self, db_session: AsyncSession, user: User
    ) -> None:
        result = await category_trends(
            db_session, user_id=user.id, anchor=date(2026, 4, 30), months=3
        )
        assert result.categories == []
        assert result.buckets == []
        # ``months`` is still populated so the front-end has axis labels.
        assert len(result.months) == 3

    async def test_excludes_other_users(self, db_session: AsyncSession, user: User) -> None:
        stranger = await create_user(
            db_session, email=f"trends-{uuid4()}@example.com", password="hunter2hunter2"
        )
        await _add_expense(
            db_session,
            user_id=stranger.id,
            amount="500.00",
            category=ExpenseCategory.TRAVEL,
            on_date=date(2026, 3, 15),
        )

        result = await category_trends(
            db_session, user_id=user.id, anchor=date(2026, 4, 30), months=3
        )
        assert result.categories == []


@pytest.mark.parametrize(
    "month_input,expected_start",
    [
        (date(2026, 1, 1), date(2026, 1, 1)),
        (date(2026, 1, 31), date(2026, 1, 1)),
        (date(2026, 12, 31), date(2026, 12, 1)),
    ],
)
def test_month_bounds_parametrised(month_input: date, expected_start: date) -> None:
    start, _ = month_bounds(month_input)
    assert start == expected_start

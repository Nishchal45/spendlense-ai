"""Aggregate analytics over the ``expenses`` table.

Every read is a single round trip to Postgres — Phase 6 deliberately
avoids a second analytics store. The dashboard's hot path is a few
seconds per pageload at most, and the existing
``ix_expenses_user_date`` index covers the date-range scans we lean on
here. If a user ever sits on tens of thousands of rows we'll add
materialised views; we won't pre-pay that complexity now.

Three families of query live in this module:

1. **Monthly breakdown** — total / count / average per category for a
   single calendar month. The dashboard's anchor view.
2. **Trends** — rolling N-month totals per category, suitable for a
   line chart. We always include zero buckets so the front-end doesn't
   need to reason about gaps.
3. **(Phase 6 PR #B+)** — anomaly detection and budget status. Both
   sit on top of the same row store; their service helpers will land
   in their own modules but reuse :func:`month_bounds` here.

All queries are scoped by ``user_id`` at the SQL layer (same rule as
expenses CRUD) — a router bug that forgets to pass the user fails
closed (empty result) rather than leaking another tenant's spend.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Float, Numeric, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExpenseCategory
from app.models.expense import Expense

# Number of months we render in the trends chart by default.
# Twelve is the natural finance horizon (year-over-year comparisons
# fall out for free) and matches the canonical "2024 in spending"
# UX. Callers can override but rarely need to.
DEFAULT_TRENDS_MONTHS = 12

# Maximum trends window we accept on the wire. The query is cheap
# but unbounded windows on a hostile client would still let a single
# user pin a connection. Five years is plenty.
MAX_TRENDS_MONTHS = 60


# ----- shared helpers ------------------------------------------------------


_DECEMBER = 12


def month_bounds(month: date) -> tuple[date, date]:
    """Return ``[first_of_month, first_of_next_month)`` for ``month``.

    The half-open interval is what every aggregate below uses — it
    avoids the inclusive-vs-exclusive footgun on month boundaries
    that creeps in when callers do their own arithmetic.
    """
    start = month.replace(day=1)
    end = (
        date(start.year + 1, 1, 1)
        if start.month == _DECEMBER
        else date(start.year, start.month + 1, 1)
    )
    return start, end


def _months_back(anchor: date, n: int) -> date:
    """Return the first day of the month ``n`` months before ``anchor``.

    ``anchor`` is treated as if it were already the 1st — callers pass
    a real date and we do the calendar math.
    """
    year = anchor.year
    month = anchor.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


# ----- monthly breakdown ---------------------------------------------------


@dataclass(frozen=True)
class CategoryTotal:
    """One row of the monthly-breakdown response."""

    category: ExpenseCategory
    total: Decimal
    count: int
    average: Decimal


@dataclass(frozen=True)
class MonthlyBreakdown:
    """Single-month rollup. ``items`` is sorted by ``total`` desc so
    the biggest categories render first."""

    month: date  # first day of the month
    grand_total: Decimal
    grand_count: int
    items: list[CategoryTotal]


async def monthly_breakdown(
    session: AsyncSession,
    *,
    user_id: UUID,
    month: date,
) -> MonthlyBreakdown:
    """Per-category spend totals for a single calendar month."""
    start, end = month_bounds(month)

    stmt = (
        select(
            Expense.category,
            func.sum(Expense.amount).label("total"),
            # ``row_count`` rather than ``count`` to dodge a name
            # collision with ``Row.count`` the typed-row method;
            # mypy can't disambiguate the two and flags the field
            # access as ``Callable``.
            func.count().label("row_count"),
            # Cast to Numeric explicitly — ``avg`` over Numeric returns
            # ``numeric`` but SQLAlchemy's reflection sometimes types
            # it as ``Decimal | None`` even when the row count is
            # zero, which we filter out anyway.
            cast(func.avg(Expense.amount), Numeric(12, 2)).label("average"),
        )
        .where(
            Expense.user_id == user_id,
            Expense.expense_date >= start,
            Expense.expense_date < end,
        )
        .group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )

    rows = (await session.execute(stmt)).all()
    items = [
        CategoryTotal(
            category=row.category,
            total=row.total,
            count=row.row_count,
            average=row.average,
        )
        for row in rows
    ]
    grand_total = sum((item.total for item in items), Decimal("0"))
    grand_count = sum(item.count for item in items)

    return MonthlyBreakdown(
        month=start,
        grand_total=grand_total,
        grand_count=grand_count,
        items=items,
    )


# ----- trends --------------------------------------------------------------


@dataclass(frozen=True)
class TrendBucket:
    """One (month, category) cell of the trend matrix."""

    month: date
    category: ExpenseCategory
    total: Decimal


@dataclass(frozen=True)
class CategoryTrends:
    """Rolling N-month spend per category. ``buckets`` is dense — every
    (month, category) combination in the window is present, so a
    front-end chart doesn't need to reason about gaps.
    """

    months: list[date]  # length == requested window, ascending
    categories: list[ExpenseCategory]  # categories that had any spend in the window
    buckets: list[TrendBucket]


async def category_trends(
    session: AsyncSession,
    *,
    user_id: UUID,
    anchor: date,
    months: int = DEFAULT_TRENDS_MONTHS,
) -> CategoryTrends:
    """Per-category totals for each of the last ``months`` months.

    ``anchor`` is "the month the user is looking at"; we walk back
    ``months - 1`` months from there. The window is inclusive on both
    ends, so ``months=12`` returns 12 buckets per category.
    """
    months = max(1, min(months, MAX_TRENDS_MONTHS))
    anchor_first = anchor.replace(day=1)
    window_start = _months_back(anchor_first, months - 1)
    _, window_end = month_bounds(anchor_first)

    bucket_dates = [_months_back(anchor_first, i) for i in reversed(range(months))]

    stmt = (
        select(
            func.date_trunc("month", Expense.expense_date).label("month"),
            Expense.category,
            func.sum(Expense.amount).label("total"),
        )
        .where(
            Expense.user_id == user_id,
            Expense.expense_date >= window_start,
            Expense.expense_date < window_end,
        )
        .group_by("month", Expense.category)
    )
    rows = (await session.execute(stmt)).all()

    # Pivot the sparse SQL result into a dense (month × category)
    # grid. The grid is what front-end chart libs (Recharts / Chart.js)
    # expect — gap handling on the client is a perennial source of
    # off-by-one bugs.
    seen_categories = sorted({row.category for row in rows}, key=lambda c: c.value)
    totals: dict[tuple[date, ExpenseCategory], Decimal] = {}
    for row in rows:
        # ``date_trunc`` returns a timestamp-with-the-time-zeroed; the
        # date() cast is safe because every value is a month-start.
        month_date = row.month.date() if hasattr(row.month, "date") else row.month
        totals[(month_date, row.category)] = row.total

    buckets: list[TrendBucket] = []
    for month in bucket_dates:
        for category in seen_categories:
            buckets.append(
                TrendBucket(
                    month=month,
                    category=category,
                    total=totals.get((month, category), Decimal("0.00")),
                )
            )

    return CategoryTrends(
        months=bucket_dates,
        categories=seen_categories,
        buckets=buckets,
    )


# ----- anomaly detection --------------------------------------------------


# Defaults tuned for personal finance. ``180`` days of baseline gives
# the rolling stats enough samples to be meaningful without dragging
# in lifestyle changes from years past. ``30`` days of lookback is
# the canonical "this month" surface a user sees on the dashboard.
# A z-score of ``2.0`` corresponds to roughly the top 2.5% of a normal
# distribution — strict enough that the dashboard doesn't drown the
# user in noise. ``min_samples=5`` keeps stddev meaningful; below
# that the variance estimate is too unstable.
DEFAULT_BASELINE_DAYS = 180
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_Z_THRESHOLD = 2.0
MIN_BASELINE_SAMPLES = 5

# Cap the response so a hostile / buggy client (or a real user with a
# wildly anomalous month) can't ask for an unbounded list.
MAX_ANOMALY_RESULTS = 50


@dataclass(frozen=True)
class Anomaly:
    """One row of the anomaly response.

    ``z_score`` is the standardised distance from the per-category
    baseline mean. The dashboard renders this alongside ``baseline_mean``
    so the user can see the comparison directly: "$120 vs your usual
    ~$25 in coffee" reads better than a bare statistic.
    """

    expense_id: UUID
    merchant_name: str
    category: ExpenseCategory
    amount: Decimal
    expense_date: date
    z_score: float
    baseline_mean: Decimal
    baseline_stddev: Decimal
    baseline_samples: int


@dataclass(frozen=True)
class AnomalyReport:
    """Response for ``GET /insights/anomalies``."""

    lookback_start: date
    baseline_start: date
    z_threshold: float
    anomalies: list[Anomaly]


async def detect_anomalies(
    session: AsyncSession,
    *,
    user_id: UUID,
    today: date,
    baseline_days: int = DEFAULT_BASELINE_DAYS,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    z_threshold: float = DEFAULT_Z_THRESHOLD,
) -> AnomalyReport:
    """Flag recent expenses that deviate from the per-category baseline.

    Two windows define the analysis:

    * **Baseline** — ``[today - baseline_days, today - lookback_days)``.
      Per-category mean + sample stddev are computed here. Excluding
      the lookback range from the baseline is what makes the
      detector "this is unusual *for me*" instead of "this is unusual
      compared to itself".
    * **Lookback** — ``[today - lookback_days, today]``. Each expense
      in this window is scored ``(amount - mean) / stddev`` against
      the baseline of its own category. We surface the rows where
      that z-score meets ``z_threshold``.

    Categories with fewer than :data:`MIN_BASELINE_SAMPLES` baseline
    rows are skipped — stddev on three observations is meaningless
    and the dashboard can't say anything useful about them.

    Returned rows are ordered by descending z-score (most anomalous
    first) and capped at :data:`MAX_ANOMALY_RESULTS`.
    """
    baseline_start = today - timedelta(days=baseline_days)
    lookback_start = today - timedelta(days=lookback_days)

    baseline_cte = (
        select(
            Expense.category.label("category"),
            cast(func.avg(Expense.amount), Numeric(12, 2)).label("mean"),
            # ``stddev_samp`` (the sample stddev) is the right call
            # for inferential use — ``stddev_pop`` would understate
            # variance and produce too many false positives. Postgres
            # returns ``NULL`` for n=1; the ``HAVING`` clause filters
            # those before the join.
            cast(func.stddev_samp(Expense.amount), Numeric(12, 2)).label("stddev"),
            func.count().label("samples"),
        )
        .where(
            Expense.user_id == user_id,
            Expense.expense_date >= baseline_start,
            Expense.expense_date < lookback_start,
        )
        .group_by(Expense.category)
        .having(
            and_(
                func.count() >= MIN_BASELINE_SAMPLES,
                # ``> 0`` filters categories where every receipt was
                # the same amount (subscriptions etc.) — dividing by
                # zero would break the z-score and a zero-stddev
                # category genuinely has no signal to flag against.
                func.stddev_samp(Expense.amount) > 0,
            )
        )
        .cte("baseline")
    )

    z_score_expr = cast(Expense.amount - baseline_cte.c.mean, Float) / cast(
        baseline_cte.c.stddev, Float
    )

    stmt = (
        select(
            Expense.id.label("expense_id"),
            Expense.merchant_name,
            Expense.category,
            Expense.amount,
            Expense.expense_date,
            z_score_expr.label("z_score"),
            baseline_cte.c.mean.label("baseline_mean"),
            baseline_cte.c.stddev.label("baseline_stddev"),
            baseline_cte.c.samples.label("baseline_samples"),
        )
        .join(baseline_cte, baseline_cte.c.category == Expense.category)
        .where(
            Expense.user_id == user_id,
            Expense.expense_date >= lookback_start,
            Expense.expense_date <= today,
            z_score_expr >= z_threshold,
        )
        .order_by(z_score_expr.desc())
        .limit(MAX_ANOMALY_RESULTS)
    )

    rows = (await session.execute(stmt)).all()
    anomalies = [
        Anomaly(
            expense_id=row.expense_id,
            merchant_name=row.merchant_name,
            category=row.category,
            amount=row.amount,
            expense_date=row.expense_date,
            z_score=float(row.z_score),
            baseline_mean=row.baseline_mean,
            baseline_stddev=row.baseline_stddev,
            baseline_samples=row.baseline_samples,
        )
        for row in rows
    ]

    return AnomalyReport(
        lookback_start=lookback_start,
        baseline_start=baseline_start,
        z_threshold=z_threshold,
        anomalies=anomalies,
    )

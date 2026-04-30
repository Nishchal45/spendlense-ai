"""Wire contracts for the insights endpoints.

The service-layer dataclasses use Python ``date`` and ``Decimal``;
these Pydantic models pin the JSON shape clients see. Decimals
serialise as strings on the wire so JS clients don't lose precision
to float coercion — same convention as the expenses surface.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.enums import ExpenseCategory


class CategoryTotalOut(BaseModel):
    """One row of the monthly breakdown."""

    model_config = ConfigDict(from_attributes=True)

    category: ExpenseCategory
    total: Decimal
    count: int
    average: Decimal


class MonthlyBreakdownOut(BaseModel):
    """Response for ``GET /insights/monthly``."""

    model_config = ConfigDict(from_attributes=True)

    month: date
    grand_total: Decimal
    grand_count: int
    items: list[CategoryTotalOut]


class TrendBucketOut(BaseModel):
    """One ``(month, category)`` cell. ``month`` is the first day of
    that month — clients render that as the bucket label."""

    model_config = ConfigDict(from_attributes=True)

    month: date
    category: ExpenseCategory
    total: Decimal


class CategoryTrendsOut(BaseModel):
    """Response for ``GET /insights/trends``.

    The ``months`` and ``categories`` arrays let a chart library
    pre-allocate axes; ``buckets`` is the dense grid in row-major
    order (month-by-month, then category-by-category). A front-end
    that wants a Recharts-style series array can group buckets by
    ``category`` in one pass.
    """

    model_config = ConfigDict(from_attributes=True)

    months: list[date]
    categories: list[ExpenseCategory]
    buckets: list[TrendBucketOut]


class AnomalyOut(BaseModel):
    """One row of the anomaly response."""

    model_config = ConfigDict(from_attributes=True)

    expense_id: UUID
    merchant_name: str
    category: ExpenseCategory
    amount: Decimal
    expense_date: date
    z_score: float
    baseline_mean: Decimal
    baseline_stddev: Decimal
    baseline_samples: int


class AnomalyReportOut(BaseModel):
    """Response for ``GET /insights/anomalies``.

    The window dates and threshold are echoed back to the client so
    the UI can render "we looked at the last 30 days against the
    previous 6 months" without re-doing the date math.
    """

    model_config = ConfigDict(from_attributes=True)

    lookback_start: date
    baseline_start: date
    z_threshold: float
    anomalies: list[AnomalyOut]

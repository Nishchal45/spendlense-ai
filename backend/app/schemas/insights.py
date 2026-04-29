"""Wire contracts for the insights endpoints.

The service-layer dataclasses use Python ``date`` and ``Decimal``;
these Pydantic models pin the JSON shape clients see. Decimals
serialise as strings on the wire so JS clients don't lose precision
to float coercion — same convention as the expenses surface.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

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

"""Insights surface — monthly breakdowns and category trends.

Routes are intentionally thin: parse the query string, delegate to
``insights_service``, return a Pydantic projection. Anomalies and
budget-status endpoints land in subsequent PRs on the same router.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

import structlog
from fastapi import APIRouter, HTTPException, Query, status

from app.api.v1.deps import CurrentUser, SessionDep
from app.schemas.insights import CategoryTrendsOut, MonthlyBreakdownOut
from app.services.insights_service import (
    DEFAULT_TRENDS_MONTHS,
    MAX_TRENDS_MONTHS,
    category_trends,
    monthly_breakdown,
)

router = APIRouter(prefix="/insights", tags=["insights"])
log = structlog.get_logger()


@router.get("/monthly", response_model=MonthlyBreakdownOut)
async def get_monthly_breakdown(
    current_user: CurrentUser,
    session: SessionDep,
    month: Annotated[
        str | None,
        Query(
            description="ISO-8601 date inside the target month, e.g. ``2026-04-01``. "
            "Defaults to today.",
        ),
    ] = None,
) -> MonthlyBreakdownOut:
    """Per-category spend totals for one calendar month.

    The ``month`` parameter takes any date inside the month — we
    snap to the 1st in the service layer. Strict ``YYYY-MM`` parsing
    is deliberately rejected because the parser would have to do
    extra work for a value the service already coerces.
    """
    target = _parse_month_or_today(month)
    result = await monthly_breakdown(session, user_id=current_user.id, month=target)
    return MonthlyBreakdownOut.model_validate(result)


@router.get("/trends", response_model=CategoryTrendsOut)
async def get_category_trends(
    current_user: CurrentUser,
    session: SessionDep,
    anchor: Annotated[
        str | None,
        Query(
            description="ISO-8601 date inside the most-recent month to render. Defaults to today.",
        ),
    ] = None,
    months: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_TRENDS_MONTHS,
            description="How many months back to include (inclusive of ``anchor``).",
        ),
    ] = DEFAULT_TRENDS_MONTHS,
) -> CategoryTrendsOut:
    """Rolling per-category totals across the last ``months`` months."""
    target = _parse_month_or_today(anchor)
    result = await category_trends(session, user_id=current_user.id, anchor=target, months=months)
    return CategoryTrendsOut.model_validate(result)


def _parse_month_or_today(value: str | None) -> date:
    """Coerce an optional ISO-8601 string to a ``date`` or default to today.

    422 on a bad value rather than a 500 — surfacing parse errors at
    the FastAPI boundary keeps the service layer focused on real
    queries instead of input validation.
    """
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid ISO-8601 date: {value}",
        ) from exc

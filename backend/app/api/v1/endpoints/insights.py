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
from app.schemas.insights import AnomalyReportOut, CategoryTrendsOut, MonthlyBreakdownOut
from app.services.insights_service import (
    DEFAULT_BASELINE_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_TRENDS_MONTHS,
    DEFAULT_Z_THRESHOLD,
    MAX_TRENDS_MONTHS,
    category_trends,
    detect_anomalies,
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


@router.get("/anomalies", response_model=AnomalyReportOut)
async def get_anomalies(
    current_user: CurrentUser,
    session: SessionDep,
    today: Annotated[
        str | None,
        Query(
            description=(
                "ISO-8601 date treated as 'today' for the analysis. " "Defaults to actual today."
            ),
        ),
    ] = None,
    baseline_days: Annotated[
        int,
        Query(
            ge=30,
            le=730,
            description="Days of baseline history to compute the per-category mean.",
        ),
    ] = DEFAULT_BASELINE_DAYS,
    lookback_days: Annotated[
        int,
        Query(
            ge=1,
            le=180,
            description="Days back from ``today`` to flag anomalies in.",
        ),
    ] = DEFAULT_LOOKBACK_DAYS,
    z_threshold: Annotated[
        float,
        Query(
            ge=0.5,
            le=10.0,
            description="Standard-deviation threshold; ``2.0`` is the default.",
        ),
    ] = DEFAULT_Z_THRESHOLD,
) -> AnomalyReportOut:
    """Recent expenses that deviate from each category's baseline.

    Per-category baseline (mean + sample stddev) is computed over
    ``[today - baseline_days, today - lookback_days)``. Each expense
    in the lookback window is z-scored against its category's
    baseline; rows >= ``z_threshold`` are surfaced.

    422 on a bad ``today``, on the same theory as ``/monthly``.
    """
    target = _parse_month_or_today(today)
    if lookback_days >= baseline_days:
        # The baseline window must end *before* the lookback window;
        # otherwise the analysis is comparing the lookback to itself.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="lookback_days must be smaller than baseline_days",
        )

    result = await detect_anomalies(
        session,
        user_id=current_user.id,
        today=target,
        baseline_days=baseline_days,
        lookback_days=lookback_days,
        z_threshold=z_threshold,
    )
    return AnomalyReportOut.model_validate(result)


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

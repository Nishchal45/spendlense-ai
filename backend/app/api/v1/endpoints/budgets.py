"""Budget CRUD + status endpoint.

Same shape as the expenses surface: thin routes, ownership enforced
in the service-layer queries, 404 on cross-user access. The status
sub-route is the only one that touches actual spend data — CRUD is
pure-table hygiene.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.v1.deps import CurrentUser, SessionDep
from app.schemas.budget import (
    BudgetCreate,
    BudgetList,
    BudgetOut,
    BudgetStatusReportOut,
    BudgetUpdate,
)
from app.services.budget_service import (
    BudgetAlreadyExistsError,
    BudgetNotFoundError,
    budget_status,
    create_budget,
    delete_budget,
    get_budget,
    list_budgets,
    update_budget,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])
log = structlog.get_logger()


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post("", response_model=BudgetOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: BudgetCreate,
    current_user: CurrentUser,
    session: SessionDep,
) -> BudgetOut:
    try:
        budget = await create_budget(session, user_id=current_user.id, payload=payload.model_dump())
    except BudgetAlreadyExistsError as exc:
        raise _conflict(f"A budget already exists for {exc}") from exc

    log.info(
        "budgets.created",
        user_id=str(current_user.id),
        budget_id=str(budget.id),
        category=budget.category.value,
    )
    return BudgetOut.model_validate(budget)


@router.get("", response_model=BudgetList)
async def list_(
    current_user: CurrentUser,
    session: SessionDep,
    include_inactive: Annotated[
        bool,
        Query(description="Include paused (``active=false``) budgets in the list."),
    ] = False,
) -> BudgetList:
    rows = await list_budgets(session, user_id=current_user.id, include_inactive=include_inactive)
    return BudgetList(items=[BudgetOut.model_validate(b) for b in rows])


# ``/status`` is registered *before* the ``/{budget_id}`` route so
# FastAPI's path matcher hits the literal first — otherwise ``status``
# would parse as a UUID, fail validation, and return 422.
@router.get("/status", response_model=BudgetStatusReportOut)
async def status_endpoint(
    current_user: CurrentUser,
    session: SessionDep,
    today: Annotated[
        str | None,
        Query(
            description=(
                "ISO-8601 date treated as 'today' for the status calculation. "
                "Defaults to actual today."
            ),
        ),
    ] = None,
) -> BudgetStatusReportOut:
    """Spend-vs-budget for every active monthly budget the user has."""
    target = _parse_date_or_today(today)
    result = await budget_status(session, user_id=current_user.id, today=target)
    return BudgetStatusReportOut.model_validate(result)


@router.get("/{budget_id}", response_model=BudgetOut)
async def get(
    budget_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> BudgetOut:
    try:
        budget = await get_budget(session, user_id=current_user.id, budget_id=budget_id)
    except BudgetNotFoundError as exc:
        raise _not_found() from exc
    return BudgetOut.model_validate(budget)


@router.patch("/{budget_id}", response_model=BudgetOut)
async def patch(
    budget_id: UUID,
    payload: BudgetUpdate,
    current_user: CurrentUser,
    session: SessionDep,
) -> BudgetOut:
    patch_dict = payload.model_dump(exclude_unset=True)
    if not patch_dict:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Patch body is empty")

    try:
        budget = await update_budget(
            session, user_id=current_user.id, budget_id=budget_id, patch=patch_dict
        )
    except BudgetNotFoundError as exc:
        raise _not_found() from exc
    except BudgetAlreadyExistsError as exc:
        raise _conflict(f"A budget already exists for {exc}") from exc

    log.info(
        "budgets.updated",
        user_id=str(current_user.id),
        budget_id=str(budget_id),
        fields=list(patch_dict.keys()),
    )
    return BudgetOut.model_validate(budget)


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    budget_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> Response:
    try:
        await delete_budget(session, user_id=current_user.id, budget_id=budget_id)
    except BudgetNotFoundError as exc:
        raise _not_found() from exc

    log.info(
        "budgets.deleted",
        user_id=str(current_user.id),
        budget_id=str(budget_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _parse_date_or_today(value: str | None) -> date:
    if value is None:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid ISO-8601 date: {value}",
        ) from exc

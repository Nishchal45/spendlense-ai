"""Manual expense CRUD.

Routes are intentionally thin — parse/validate, delegate to
``expense_service``, translate domain exceptions to HTTP. Ownership is
already enforced at the query layer; here we only translate
``ExpenseNotFoundError`` to 404 so existence of another user's row
can't be probed.

ETag / If-Match: every single-resource response carries a weak ETag
derived from ``updated_at``. ``PATCH`` and ``DELETE`` accept an
optional ``If-Match`` header; if provided and stale, we return 412
Precondition Failed. Clients that don't care about concurrency can
omit the header and the server won't gate the write — the router's
contract, not the service's.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated
from uuid import UUID

import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.api.v1.deps import CurrentUser, SessionDep
from app.core.pagination import ExpenseCursor, InvalidCursorError
from app.models.enums import ExpenseCategory
from app.schemas.expense import (
    ExpenseCreate,
    ExpenseOut,
    ExpenseUpdate,
    PaginatedExpenses,
)
from app.services.expense_service import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ETagMismatchError,
    ExpenseFilters,
    ExpenseNotFoundError,
    compute_etag,
    create_expense,
    delete_expense,
    get_expense,
    list_expenses,
    update_expense,
)

router = APIRouter(prefix="/expenses", tags=["expenses"])
log = structlog.get_logger()


def _not_found() -> HTTPException:
    # 404 not 403: we don't want to leak existence of rows the caller
    # doesn't own.
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found")


def _precondition_failed() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_412_PRECONDITION_FAILED,
        detail="Expense was modified; re-fetch and retry",
    )


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create(
    payload: ExpenseCreate,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> ExpenseOut:
    expense = await create_expense(
        session,
        user_id=current_user.id,
        payload=payload.model_dump(),
    )
    response.headers["ETag"] = compute_etag(expense)
    log.info(
        "expenses.created",
        user_id=str(current_user.id),
        expense_id=str(expense.id),
    )
    return ExpenseOut.model_validate(expense)


@router.get("", response_model=PaginatedExpenses)
async def list_(
    current_user: CurrentUser,
    session: SessionDep,
    category: ExpenseCategory | None = None,
    merchant: Annotated[str | None, Query(max_length=255)] = None,
    date_from: Annotated[str | None, Query(alias="date_from")] = None,
    date_to: Annotated[str | None, Query(alias="date_to")] = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    cursor: str | None = None,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> PaginatedExpenses:
    from datetime import date as _date

    parsed_from = _date.fromisoformat(date_from) if date_from else None
    parsed_to = _date.fromisoformat(date_to) if date_to else None

    filters = ExpenseFilters(
        category=category,
        merchant_query=merchant,
        date_from=parsed_from,
        date_to=parsed_to,
        min_amount=min_amount,
        max_amount=max_amount,
    )

    decoded_cursor: ExpenseCursor | None = None
    if cursor:
        try:
            decoded_cursor = ExpenseCursor.decode(cursor)
        except InvalidCursorError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid cursor",
            ) from exc

    page = await list_expenses(
        session,
        user_id=current_user.id,
        filters=filters,
        cursor=decoded_cursor,
        page_size=page_size,
    )
    return PaginatedExpenses(
        items=[ExpenseOut.model_validate(e) for e in page.items],
        next_cursor=page.next_cursor.encode() if page.next_cursor else None,
    )


@router.get("/{expense_id}", response_model=ExpenseOut)
async def get(
    expense_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> ExpenseOut:
    try:
        expense = await get_expense(session, user_id=current_user.id, expense_id=expense_id)
    except ExpenseNotFoundError as exc:
        raise _not_found() from exc
    response.headers["ETag"] = compute_etag(expense)
    return ExpenseOut.model_validate(expense)


@router.patch("/{expense_id}", response_model=ExpenseOut)
async def patch(
    expense_id: UUID,
    payload: ExpenseUpdate,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ExpenseOut:
    patch_dict = payload.model_dump(exclude_unset=True)
    if not patch_dict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patch body is empty",
        )
    try:
        expense = await update_expense(
            session,
            user_id=current_user.id,
            expense_id=expense_id,
            patch=patch_dict,
            if_match=if_match,
        )
    except ExpenseNotFoundError as exc:
        raise _not_found() from exc
    except ETagMismatchError as exc:
        raise _precondition_failed() from exc

    response.headers["ETag"] = compute_etag(expense)
    log.info(
        "expenses.updated",
        user_id=str(current_user.id),
        expense_id=str(expense.id),
        fields=list(patch_dict.keys()),
    )
    return ExpenseOut.model_validate(expense)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    expense_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    try:
        await delete_expense(
            session,
            user_id=current_user.id,
            expense_id=expense_id,
            if_match=if_match,
        )
    except ExpenseNotFoundError as exc:
        raise _not_found() from exc
    except ETagMismatchError as exc:
        raise _precondition_failed() from exc

    log.info(
        "expenses.deleted",
        user_id=str(current_user.id),
        expense_id=str(expense_id),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

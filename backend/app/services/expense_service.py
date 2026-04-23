"""Expense persistence and query logic.

Everything that reads or writes the ``expenses`` table goes through
here. Routes stay thin — they validate, call into this module, and map
domain exceptions to HTTP status codes.

Ownership is enforced **in every query** via a ``user_id`` clause, not
via "load the row then compare ids in Python". That way a bug in the
router can't turn into a cross-tenant leak — the wrong user simply
sees 404 Not Found.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import ExpenseCursor
from app.models.enums import ExpenseCategory, ExpenseSource
from app.models.expense import Expense


class ExpenseNotFoundError(Exception):
    """Raised when an expense does not exist — or does not belong to the
    requesting user. We deliberately don't distinguish the two cases at
    the router layer so existence can't be probed.
    """


class ETagMismatchError(Exception):
    """Raised when a mutating request carries a stale ``If-Match`` header."""


# Cap the page size so a hostile / buggy client can't ask for 10 000
# rows and tie up a DB connection. Tuned for "looks fine in a list UI".
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


@dataclass(frozen=True)
class ExpenseFilters:
    """Query filters for ``list_expenses``. All optional, all ANDed."""

    category: ExpenseCategory | None = None
    merchant_query: str | None = None  # case-insensitive substring
    date_from: date | None = None
    date_to: date | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None


@dataclass(frozen=True)
class ExpensePage:
    """Result envelope for ``list_expenses``."""

    items: list[Expense]
    next_cursor: ExpenseCursor | None


def compute_etag(expense: Expense) -> str:
    """Deterministic ETag for optimistic concurrency.

    ``updated_at`` alone would be fine on single-node Postgres, but
    hashing ``(id, updated_at)`` makes the token opaque and invalidates
    on any column change that bumps ``updated_at`` via the ORM's
    ``onupdate`` hook. Weak ETag prefix (``W/``) because the
    representation is JSON with no byte-for-byte guarantee (field
    ordering etc.).
    """
    digest = hashlib.sha256(f"{expense.id}:{expense.updated_at.isoformat()}".encode()).hexdigest()
    return f'W/"{digest[:32]}"'


async def create_expense(
    session: AsyncSession,
    *,
    user_id: UUID,
    payload: dict[str, Any],
) -> Expense:
    """Insert a manual expense for ``user_id`` and return the refreshed row."""
    expense = Expense(
        user_id=user_id,
        source=ExpenseSource.MANUAL,
        **payload,
    )
    session.add(expense)
    await session.flush()
    await session.commit()
    await session.refresh(expense)
    return expense


async def get_expense(
    session: AsyncSession,
    *,
    user_id: UUID,
    expense_id: UUID,
) -> Expense:
    """Return the expense owned by ``user_id`` or raise ``ExpenseNotFoundError``."""
    stmt = select(Expense).where(and_(Expense.id == expense_id, Expense.user_id == user_id))
    result = (await session.execute(stmt)).scalar_one_or_none()
    if result is None:
        raise ExpenseNotFoundError(str(expense_id))
    return result


def _apply_filters(stmt: Select[tuple[Expense]], filters: ExpenseFilters) -> Select[tuple[Expense]]:
    if filters.category is not None:
        stmt = stmt.where(Expense.category == filters.category)
    if filters.merchant_query:
        # ILIKE is trivially indexable via ``pg_trgm`` if it becomes hot.
        # Not worth paying for that index on day one.
        stmt = stmt.where(Expense.merchant_name.ilike(f"%{filters.merchant_query}%"))
    if filters.date_from is not None:
        stmt = stmt.where(Expense.expense_date >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Expense.expense_date <= filters.date_to)
    if filters.min_amount is not None:
        stmt = stmt.where(Expense.amount >= filters.min_amount)
    if filters.max_amount is not None:
        stmt = stmt.where(Expense.amount <= filters.max_amount)
    return stmt


async def list_expenses(
    session: AsyncSession,
    *,
    user_id: UUID,
    filters: ExpenseFilters,
    cursor: ExpenseCursor | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> ExpensePage:
    """Return a page of the user's expenses, newest first.

    Sort key is ``(expense_date DESC, id DESC)`` — ``id`` is the
    tiebreaker so same-day rows page deterministically. The
    ``ix_expenses_user_date`` index covers the ORDER BY when filtered by
    ``user_id``.

    We fetch ``page_size + 1`` rows to detect whether a next page
    exists without a second COUNT query.
    """
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

    stmt: Select[tuple[Expense]] = select(Expense).where(Expense.user_id == user_id)
    stmt = _apply_filters(stmt, filters)

    if cursor is not None:
        # Standard keyset predicate for (date DESC, id DESC):
        #   date < cursor.date  OR  (date = cursor.date AND id < cursor.id)
        stmt = stmt.where(
            or_(
                Expense.expense_date < cursor.expense_date,
                and_(
                    Expense.expense_date == cursor.expense_date,
                    Expense.id < cursor.id,
                ),
            )
        )

    stmt = stmt.order_by(Expense.expense_date.desc(), Expense.id.desc()).limit(page_size + 1)

    rows = list((await session.execute(stmt)).scalars().all())

    next_cursor: ExpenseCursor | None = None
    if len(rows) > page_size:
        last = rows[page_size - 1]
        next_cursor = ExpenseCursor(expense_date=last.expense_date, id=last.id)
        rows = rows[:page_size]

    return ExpensePage(items=rows, next_cursor=next_cursor)


async def update_expense(
    session: AsyncSession,
    *,
    user_id: UUID,
    expense_id: UUID,
    patch: dict[str, Any],
    if_match: str | None = None,
) -> Expense:
    """Partial-update an expense.

    * ``patch`` must already be the result of
      ``model_dump(exclude_unset=True)`` — unset fields don't appear and
      therefore don't touch the row.
    * If ``if_match`` is provided, it must equal the current ETag.
      Mismatch raises ``ETagMismatchError`` (router maps to 412).
    * Missing ``if_match`` is allowed so clients that don't care about
      concurrency (CLI scripts) can still patch — the router decides
      whether to require it.
    """
    expense = await get_expense(session, user_id=user_id, expense_id=expense_id)

    if if_match is not None and if_match != compute_etag(expense):
        raise ETagMismatchError(str(expense_id))

    for field, value in patch.items():
        setattr(expense, field, value)

    await session.flush()
    await session.commit()
    await session.refresh(expense)
    return expense


async def delete_expense(
    session: AsyncSession,
    *,
    user_id: UUID,
    expense_id: UUID,
    if_match: str | None = None,
) -> None:
    """Delete an expense the user owns. 404 if it doesn't exist."""
    expense = await get_expense(session, user_id=user_id, expense_id=expense_id)

    if if_match is not None and if_match != compute_etag(expense):
        raise ETagMismatchError(str(expense_id))

    await session.delete(expense)
    await session.commit()

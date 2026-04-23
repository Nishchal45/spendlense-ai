"""Service-layer tests for the expenses module.

These exercise the DB logic directly (no HTTP, no routes) so regressions
in query construction or cursor paging show up at the right layer.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExpenseCategory
from app.models.user import User
from app.services.expense_service import (
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


async def _make_user(db_session: AsyncSession, email: str) -> User:
    user = User(email=email, password_hash="not-a-real-hash")
    db_session.add(user)
    await db_session.flush()
    return user


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "merchant_name": "Blue Bottle",
        "amount": Decimal("4.75"),
        "currency": "USD",
        "category": ExpenseCategory.FOOD_DINING,
        "expense_date": date(2026, 4, 20),
        "description": "Morning coffee",
    }
    base.update(overrides)
    return base


class TestCreate:
    async def test_create_persists_expense(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "create@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        assert expense.id is not None
        assert expense.user_id == user.id
        assert expense.amount == Decimal("4.75")
        assert expense.source.value == "manual"


class TestGet:
    async def test_get_returns_owned_expense(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "get@example.com")
        created = await create_expense(db_session, user_id=user.id, payload=_payload())
        fetched = await get_expense(db_session, user_id=user.id, expense_id=created.id)
        assert fetched.id == created.id

    async def test_get_hides_other_users_expense(self, db_session: AsyncSession) -> None:
        owner = await _make_user(db_session, "owner@example.com")
        stranger = await _make_user(db_session, "stranger@example.com")
        created = await create_expense(db_session, user_id=owner.id, payload=_payload())

        with pytest.raises(ExpenseNotFoundError):
            await get_expense(db_session, user_id=stranger.id, expense_id=created.id)

    async def test_get_missing_row_raises(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "missing@example.com")
        with pytest.raises(ExpenseNotFoundError):
            await get_expense(db_session, user_id=user.id, expense_id=uuid4())


class TestList:
    async def test_list_filters_by_owner(self, db_session: AsyncSession) -> None:
        owner = await _make_user(db_session, "list-owner@example.com")
        stranger = await _make_user(db_session, "list-stranger@example.com")
        await create_expense(db_session, user_id=owner.id, payload=_payload())
        await create_expense(db_session, user_id=stranger.id, payload=_payload())

        page = await list_expenses(db_session, user_id=owner.id, filters=ExpenseFilters())
        assert len(page.items) == 1
        assert page.items[0].user_id == owner.id

    async def test_list_orders_newest_first(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "order@example.com")
        older = await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(expense_date=date(2026, 1, 1), merchant_name="Old"),
        )
        newer = await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(expense_date=date(2026, 4, 1), merchant_name="New"),
        )

        page = await list_expenses(db_session, user_id=user.id, filters=ExpenseFilters())
        assert [e.id for e in page.items] == [newer.id, older.id]

    async def test_list_filters_by_category(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "catfilter@example.com")
        await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(category=ExpenseCategory.FOOD_DINING),
        )
        groceries = await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(category=ExpenseCategory.GROCERIES, merchant_name="Safeway"),
        )

        page = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(category=ExpenseCategory.GROCERIES),
        )
        assert [e.id for e in page.items] == [groceries.id]

    async def test_list_filters_by_merchant_substring_ci(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "merchfilter@example.com")
        await create_expense(db_session, user_id=user.id, payload=_payload(merchant_name="UBER"))
        await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(merchant_name="Lyft", category=ExpenseCategory.TRANSPORTATION),
        )

        page = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(merchant_query="uber"),
        )
        assert len(page.items) == 1
        assert page.items[0].merchant_name == "UBER"

    async def test_list_filters_by_date_range(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "dates@example.com")
        await create_expense(
            db_session, user_id=user.id, payload=_payload(expense_date=date(2026, 1, 1))
        )
        in_range = await create_expense(
            db_session, user_id=user.id, payload=_payload(expense_date=date(2026, 3, 15))
        )
        await create_expense(
            db_session, user_id=user.id, payload=_payload(expense_date=date(2026, 6, 1))
        )

        page = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(date_from=date(2026, 2, 1), date_to=date(2026, 4, 1)),
        )
        assert [e.id for e in page.items] == [in_range.id]

    async def test_list_filters_by_amount_range(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "amts@example.com")
        await create_expense(db_session, user_id=user.id, payload=_payload(amount=Decimal("1.00")))
        mid = await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(amount=Decimal("25.00"), merchant_name="Mid"),
        )
        await create_expense(
            db_session,
            user_id=user.id,
            payload=_payload(amount=Decimal("500.00"), merchant_name="Big"),
        )

        page = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(min_amount=Decimal("10.00"), max_amount=Decimal("100.00")),
        )
        assert [e.id for e in page.items] == [mid.id]

    async def test_list_paginates_via_cursor(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "pager@example.com")
        # Create 5 rows on consecutive days so the sort order is unambiguous.
        created_ids = []
        for day in range(5):
            row = await create_expense(
                db_session,
                user_id=user.id,
                payload=_payload(
                    expense_date=date(2026, 4, 1) + timedelta(days=day),
                    merchant_name=f"Merchant {day}",
                ),
            )
            created_ids.append(row.id)

        # Newest first, page_size=2 → three pages: [4,3], [2,1], [0]
        page1 = await list_expenses(
            db_session, user_id=user.id, filters=ExpenseFilters(), page_size=2
        )
        assert [e.id for e in page1.items] == [created_ids[4], created_ids[3]]
        assert page1.next_cursor is not None

        page2 = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(),
            cursor=page1.next_cursor,
            page_size=2,
        )
        assert [e.id for e in page2.items] == [created_ids[2], created_ids[1]]
        assert page2.next_cursor is not None

        page3 = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(),
            cursor=page2.next_cursor,
            page_size=2,
        )
        assert [e.id for e in page3.items] == [created_ids[0]]
        assert page3.next_cursor is None

    async def test_list_caps_page_size(self, db_session: AsyncSession) -> None:
        # Page size > MAX_PAGE_SIZE is silently clamped.
        user = await _make_user(db_session, "cap@example.com")
        await create_expense(db_session, user_id=user.id, payload=_payload())
        page = await list_expenses(
            db_session,
            user_id=user.id,
            filters=ExpenseFilters(),
            page_size=10_000,
        )
        assert len(page.items) == 1


class TestUpdate:
    async def test_update_applies_partial_patch(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "patch@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        updated = await update_expense(
            db_session,
            user_id=user.id,
            expense_id=expense.id,
            patch={"merchant_name": "Philz Coffee"},
        )
        assert updated.merchant_name == "Philz Coffee"
        # Untouched fields survive the patch.
        assert updated.amount == Decimal("4.75")

    async def test_update_rejects_other_users_expense(self, db_session: AsyncSession) -> None:
        owner = await _make_user(db_session, "upd-owner@example.com")
        stranger = await _make_user(db_session, "upd-stranger@example.com")
        expense = await create_expense(db_session, user_id=owner.id, payload=_payload())

        with pytest.raises(ExpenseNotFoundError):
            await update_expense(
                db_session,
                user_id=stranger.id,
                expense_id=expense.id,
                patch={"merchant_name": "hacked"},
            )

    async def test_update_with_matching_etag_succeeds(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "etag-ok@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        etag = compute_etag(expense)

        updated = await update_expense(
            db_session,
            user_id=user.id,
            expense_id=expense.id,
            patch={"merchant_name": "Fresh Name"},
            if_match=etag,
        )
        assert updated.merchant_name == "Fresh Name"
        # ETag rotates after the update.
        assert compute_etag(updated) != etag

    async def test_update_with_stale_etag_raises(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "etag-stale@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        with pytest.raises(ETagMismatchError):
            await update_expense(
                db_session,
                user_id=user.id,
                expense_id=expense.id,
                patch={"merchant_name": "conflict"},
                if_match='W/"not-the-real-etag"',
            )


class TestDelete:
    async def test_delete_removes_owned_expense(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "del@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        await delete_expense(db_session, user_id=user.id, expense_id=expense.id)

        with pytest.raises(ExpenseNotFoundError):
            await get_expense(db_session, user_id=user.id, expense_id=expense.id)

    async def test_delete_rejects_other_users_expense(self, db_session: AsyncSession) -> None:
        owner = await _make_user(db_session, "del-owner@example.com")
        stranger = await _make_user(db_session, "del-stranger@example.com")
        expense = await create_expense(db_session, user_id=owner.id, payload=_payload())

        with pytest.raises(ExpenseNotFoundError):
            await delete_expense(db_session, user_id=stranger.id, expense_id=expense.id)
        # Row still exists for the real owner.
        still_there = await get_expense(db_session, user_id=owner.id, expense_id=expense.id)
        assert still_there.id == expense.id

    async def test_delete_with_stale_etag_raises(self, db_session: AsyncSession) -> None:
        user = await _make_user(db_session, "del-etag@example.com")
        expense = await create_expense(db_session, user_id=user.id, payload=_payload())
        with pytest.raises(ETagMismatchError):
            await delete_expense(
                db_session,
                user_id=user.id,
                expense_id=expense.id,
                if_match='W/"not-the-real-etag"',
            )

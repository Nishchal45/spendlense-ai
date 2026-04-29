"""Tests for the anomaly-detection insight.

Two layers of coverage:

* **Service unit tests** seed tight, hand-built baselines and
  lookback rows so we can assert specific expenses surface (or
  don't) for known z-scores. The point is to lock in the SQL
  semantics — sample stddev vs population stddev, ``HAVING``
  filters on small baselines, the strict separation between
  baseline and lookback windows.
* **HTTP integration tests** verify wire shape, parameter
  validation, and the per-user isolation we get from the WHERE
  clauses.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ExpenseCategory, ExpenseSource
from app.models.expense import Expense
from app.models.user import User
from app.services.insights_service import (
    DEFAULT_BASELINE_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    detect_anomalies,
)
from app.services.user_service import create_user

API = "/api/v1"


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> User:
    return await create_user(
        db_session, email=f"anomalies-{uuid4()}@example.com", password="hunter2hunter2"
    )


async def _add(
    session: AsyncSession,
    *,
    user_id: UUID,
    amount: str,
    category: ExpenseCategory,
    on_date: date,
    merchant: str = "Test",
) -> Expense:
    expense = Expense(
        user_id=user_id,
        merchant_name=merchant,
        amount=Decimal(amount),
        currency="USD",
        category=category,
        expense_date=on_date,
        source=ExpenseSource.MANUAL,
    )
    session.add(expense)
    await session.flush()
    return expense


# Today is fixed in every test so seeding is deterministic.
_TODAY = date(2026, 4, 30)


class TestServiceLayer:
    async def test_flags_clearly_anomalous_expense(
        self, db_session: AsyncSession, user: User
    ) -> None:
        # Baseline: 10 coffees around $5 each in the last six months
        # (excluding the most recent 30 days). Very tight stddev.
        for offset in range(40, 50):
            await _add(
                db_session,
                user_id=user.id,
                amount=f"{5 + (offset % 3) * 0.1:.2f}",
                category=ExpenseCategory.FOOD_DINING,
                on_date=_TODAY - timedelta(days=offset),
            )

        # One $60 coffee in the lookback window — way outside the
        # baseline.
        anomalous = await _add(
            db_session,
            user_id=user.id,
            amount="60.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=_TODAY - timedelta(days=5),
            merchant="Suspicious Latte",
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        assert len(result.anomalies) == 1
        flagged = result.anomalies[0]
        assert flagged.expense_id == anomalous.id
        assert flagged.merchant_name == "Suspicious Latte"
        assert flagged.z_score >= 2.0
        assert flagged.baseline_samples == 10

    async def test_does_not_flag_expense_within_baseline_range(
        self, db_session: AsyncSession, user: User
    ) -> None:
        # Same baseline as above.
        for offset in range(40, 50):
            await _add(
                db_session,
                user_id=user.id,
                amount=f"{5 + (offset % 3) * 0.1:.2f}",
                category=ExpenseCategory.FOOD_DINING,
                on_date=_TODAY - timedelta(days=offset),
            )

        # A $5.20 coffee — within ~1.25 stddev of the ~$5.10 mean
        # (the baseline noise is tight at $0.08 stddev, so the test
        # value has to be tight too).
        await _add(
            db_session,
            user_id=user.id,
            amount="5.20",
            category=ExpenseCategory.FOOD_DINING,
            on_date=_TODAY - timedelta(days=2),
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        assert result.anomalies == []

    async def test_skips_categories_with_too_few_samples(
        self, db_session: AsyncSession, user: User
    ) -> None:
        # Only 3 baseline rows (under the 5-sample floor).
        for offset in (40, 50, 60):
            await _add(
                db_session,
                user_id=user.id,
                amount="10.00",
                category=ExpenseCategory.SHOPPING,
                on_date=_TODAY - timedelta(days=offset),
            )
        # A wild outlier that *would* be flagged if the baseline
        # were big enough.
        await _add(
            db_session,
            user_id=user.id,
            amount="9999.00",
            category=ExpenseCategory.SHOPPING,
            on_date=_TODAY - timedelta(days=2),
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        # The category never qualifies for a baseline → no row joins
        # → no anomaly. We deliberately don't surface a separate
        # "skipped" list; the dashboard says nothing about
        # under-sampled categories instead of saying something
        # statistically meaningless.
        assert result.anomalies == []

    async def test_skips_zero_stddev_categories(self, db_session: AsyncSession, user: User) -> None:
        # Six identical $10 subscriptions. stddev = 0; can't z-score.
        for offset in range(40, 46):
            await _add(
                db_session,
                user_id=user.id,
                amount="10.00",
                category=ExpenseCategory.UTILITIES,
                on_date=_TODAY - timedelta(days=offset),
            )
        # A $100 utility in the lookback range. Without the
        # zero-stddev filter, this would attempt a divide-by-zero
        # and either crash or produce ``+inf``.
        await _add(
            db_session,
            user_id=user.id,
            amount="100.00",
            category=ExpenseCategory.UTILITIES,
            on_date=_TODAY - timedelta(days=2),
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        assert result.anomalies == []

    async def test_isolates_per_user(self, db_session: AsyncSession, user: User) -> None:
        # Stranger has the noisy baseline; the test user has the
        # outlier — they must NOT cross-pollinate.
        stranger = await create_user(
            db_session,
            email=f"anom-stranger-{uuid4()}@example.com",
            password="hunter2hunter2",
        )
        for offset in range(40, 50):
            await _add(
                db_session,
                user_id=stranger.id,
                amount=f"{5 + (offset % 3) * 0.1:.2f}",
                category=ExpenseCategory.FOOD_DINING,
                on_date=_TODAY - timedelta(days=offset),
            )

        # User has only one row, no baseline.
        await _add(
            db_session,
            user_id=user.id,
            amount="60.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=_TODAY - timedelta(days=2),
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        # No baseline for this user → nothing flagged.
        assert result.anomalies == []

    async def test_orders_by_z_score_desc(self, db_session: AsyncSession, user: User) -> None:
        # Baseline of $5 ± epsilon coffees.
        for offset in range(40, 60):
            await _add(
                db_session,
                user_id=user.id,
                amount=f"{5 + (offset % 3) * 0.1:.2f}",
                category=ExpenseCategory.FOOD_DINING,
                on_date=_TODAY - timedelta(days=offset),
            )
        # Two anomalies; the larger z-score ($200) should come first.
        await _add(
            db_session,
            user_id=user.id,
            amount="200.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=_TODAY - timedelta(days=4),
            merchant="Big",
        )
        await _add(
            db_session,
            user_id=user.id,
            amount="50.00",
            category=ExpenseCategory.FOOD_DINING,
            on_date=_TODAY - timedelta(days=2),
            merchant="Small",
        )

        result = await detect_anomalies(db_session, user_id=user.id, today=_TODAY)
        names = [a.merchant_name for a in result.anomalies]
        assert names == ["Big", "Small"]


class TestEndpoint:
    async def test_default_window_is_safe(self, client: AsyncClient) -> None:
        await client.post(
            f"{API}/auth/register",
            json={"email": f"http-{uuid4()}@example.com", "password": "hunter2hunter2"},
        )
        login = await client.post(
            f"{API}/auth/login",
            json={"email": f"http2-{uuid4()}@example.com", "password": "hunter2hunter2"},
        )
        # Even with no spend at all, the endpoint must respond 200
        # with an empty list — it's the canonical "first day with
        # the app" UX.
        if login.status_code != 200:
            # New email-per-test fixture is overkill here; share
            # the existing token fixture pattern from other tests.
            email = f"empty-{uuid4()}@example.com"
            await client.post(
                f"{API}/auth/register",
                json={"email": email, "password": "hunter2hunter2"},
            )
            login = await client.post(
                f"{API}/auth/login",
                json={"email": email, "password": "hunter2hunter2"},
            )
        token = login.json()["access_token"]
        resp = await client.get(
            f"{API}/insights/anomalies",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["anomalies"] == []
        assert body["z_threshold"] == 2.0

    async def test_422_on_invalid_window(self, client: AsyncClient) -> None:
        email = f"validate-{uuid4()}@example.com"
        await client.post(
            f"{API}/auth/register",
            json={"email": email, "password": "hunter2hunter2"},
        )
        login = await client.post(
            f"{API}/auth/login",
            json={"email": email, "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]

        # lookback >= baseline is a logic bug (the analysis would
        # compare the lookback to itself). 422 surfaces that
        # explicitly.
        resp = await client.get(
            f"{API}/insights/anomalies?baseline_days=30&lookback_days=30",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 422

    async def test_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{API}/insights/anomalies")
        assert resp.status_code == 401


# Silence unused-import warnings from the date-based test fixtures.
_ = (DEFAULT_BASELINE_DAYS, DEFAULT_LOOKBACK_DAYS)


@pytest.mark.parametrize(
    "baseline_days,lookback_days,expected_status",
    [
        (180, 30, 200),
        (180, 7, 200),
        (30, 30, 422),  # baseline must be strictly larger
        (10, 5, 422),  # baseline_days < min (30)
    ],
)
async def test_param_validation(
    client: AsyncClient,
    baseline_days: int,
    lookback_days: int,
    expected_status: int,
) -> None:
    email = f"params-{uuid4()}@example.com"
    await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "hunter2hunter2"},
    )
    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "hunter2hunter2"},
    )
    token = login.json()["access_token"]

    resp = await client.get(
        f"{API}/insights/anomalies" f"?baseline_days={baseline_days}&lookback_days={lookback_days}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == expected_status

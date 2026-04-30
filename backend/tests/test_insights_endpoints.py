"""Integration tests for /api/v1/insights.

The HTTP layer is intentionally thin — these tests assert wire shape,
404/422 routing, and ownership boundaries; the SQL aggregation
itself lives in ``test_insights_service.py``.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from httpx import AsyncClient

API = "/api/v1"


async def _register_and_token(client: AsyncClient, email: str) -> str:
    await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "hunter2hunter2"},
    )
    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "hunter2hunter2"},
    )
    return str(login.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expense_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "merchant_name": "Test",
        "amount": "10.00",
        "currency": "USD",
        "category": "food_dining",
        "expense_date": "2026-04-10",
    }
    base.update(overrides)
    return base


@pytest.fixture
async def token(client: AsyncClient) -> str:
    return await _register_and_token(client, f"insights-{uuid4()}@example.com")


class TestMonthlyEndpoint:
    async def test_aggregates_for_target_month(self, client: AsyncClient, token: str) -> None:
        # Two April rows, one March row.
        for date_str in ("2026-04-05", "2026-04-15"):
            await client.post(
                f"{API}/expenses",
                json=_expense_payload(amount="10.00", expense_date=date_str),
                headers=_auth(token),
            )
        await client.post(
            f"{API}/expenses",
            json=_expense_payload(amount="999.00", expense_date="2026-03-31", category="shopping"),
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/insights/monthly?month=2026-04-01", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["month"] == "2026-04-01"
        assert body["grand_total"] == "20.00"
        assert body["grand_count"] == 2
        assert len(body["items"]) == 1
        assert body["items"][0]["category"] == "food_dining"

    async def test_defaults_to_current_month(self, client: AsyncClient, token: str) -> None:
        # No ``month`` query param → service defaults to today; we
        # assert the response shape rather than the date itself
        # (which depends on the test clock).
        resp = await client.get(f"{API}/insights/monthly", headers=_auth(token))
        assert resp.status_code == 200
        assert "month" in resp.json()

    async def test_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{API}/insights/monthly?month=2026-04-01")
        assert resp.status_code == 401

    async def test_422_on_bad_date(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/insights/monthly?month=not-a-date", headers=_auth(token))
        assert resp.status_code == 422

    async def test_isolated_per_user(self, client: AsyncClient, token: str) -> None:
        # Stranger's spend must not appear in this user's breakdown.
        stranger_token = await _register_and_token(client, f"stranger-{uuid4()}@example.com")
        await client.post(
            f"{API}/expenses",
            json=_expense_payload(amount="500.00", expense_date="2026-04-10"),
            headers=_auth(stranger_token),
        )

        resp = await client.get(f"{API}/insights/monthly?month=2026-04-01", headers=_auth(token))
        assert resp.json()["grand_total"] == "0"


class TestTrendsEndpoint:
    async def test_returns_dense_grid(self, client: AsyncClient, token: str) -> None:
        await client.post(
            f"{API}/expenses",
            json=_expense_payload(amount="10.00", expense_date="2026-02-15"),
            headers=_auth(token),
        )
        await client.post(
            f"{API}/expenses",
            json=_expense_payload(amount="20.00", expense_date="2026-04-15"),
            headers=_auth(token),
        )

        resp = await client.get(
            f"{API}/insights/trends?anchor=2026-04-30&months=4", headers=_auth(token)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["months"] == [
            "2026-01-01",
            "2026-02-01",
            "2026-03-01",
            "2026-04-01",
        ]
        # 4 months × 1 category (food_dining) = 4 buckets, including
        # zeros for January and March.
        assert len(body["buckets"]) == 4
        zero_months = [b for b in body["buckets"] if b["total"] == "0.00"]
        assert len(zero_months) == 2

    async def test_default_window_is_twelve_months(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/insights/trends", headers=_auth(token))
        assert resp.status_code == 200
        assert len(resp.json()["months"]) == 12

    async def test_422_on_window_too_large(self, client: AsyncClient, token: str) -> None:
        # FastAPI's Query(le=...) handles this at the router boundary,
        # not the service.
        resp = await client.get(f"{API}/insights/trends?months=9999", headers=_auth(token))
        assert resp.status_code == 422

    async def test_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.get(f"{API}/insights/trends")
        assert resp.status_code == 401


# Silence unused-import warning from fixture imports above.
_ = date

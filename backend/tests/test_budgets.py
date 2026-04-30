"""Integration tests for /api/v1/budgets.

Locks in the HTTP contract end-to-end through the ASGI transport,
including ownership boundaries (404 for cross-user access), the
unique-constraint conflict (409), and the spend-vs-budget status
join.
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


def _budget_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "category": "food_dining",
        "amount": "200.00",
        "period": "monthly",
        "alert_threshold_pct": 80,
        "active": True,
    }
    base.update(overrides)
    return base


def _expense_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "merchant_name": "Coffee Shop",
        "amount": "10.00",
        "currency": "USD",
        "category": "food_dining",
        "expense_date": "2026-04-10",
    }
    base.update(overrides)
    return base


@pytest.fixture
async def token(client: AsyncClient) -> str:
    return await _register_and_token(client, f"budget-{uuid4()}@example.com")


class TestCreate:
    async def test_create_201(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        assert resp.status_code == 201
        body = resp.json()
        assert body["category"] == "food_dining"
        assert body["amount"] == "200.00"
        assert body["alert_threshold_pct"] == 80
        assert body["active"] is True

    async def test_409_on_duplicate_category_period(self, client: AsyncClient, token: str) -> None:
        # Two budgets for the same (food_dining, monthly) violate the
        # unique constraint — second POST is 409, not a silent overwrite.
        first = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        assert first.status_code == 201
        second = await client.post(
            f"{API}/budgets",
            json=_budget_payload(amount="500.00"),
            headers=_auth(token),
        )
        assert second.status_code == 409

    async def test_422_on_invalid_amount(self, client: AsyncClient, token: str) -> None:
        # Zero / negative budgets are nonsense — Pydantic's ``gt=0``
        # rejects at the wire boundary.
        resp = await client.post(
            f"{API}/budgets",
            json=_budget_payload(amount="0.00"),
            headers=_auth(token),
        )
        assert resp.status_code == 422

    async def test_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{API}/budgets", json=_budget_payload())
        assert resp.status_code == 401


class TestList:
    async def test_excludes_inactive_by_default(self, client: AsyncClient, token: str) -> None:
        active = await client.post(
            f"{API}/budgets", json=_budget_payload(category="groceries"), headers=_auth(token)
        )
        await client.patch(
            f"{API}/budgets/{active.json()['id']}",
            json={"active": False},
            headers=_auth(token),
        )
        await client.post(
            f"{API}/budgets",
            json=_budget_payload(category="food_dining"),
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/budgets", headers=_auth(token))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["category"] == "food_dining"

    async def test_include_inactive_query(self, client: AsyncClient, token: str) -> None:
        b = await client.post(
            f"{API}/budgets",
            json=_budget_payload(category="groceries"),
            headers=_auth(token),
        )
        await client.patch(
            f"{API}/budgets/{b.json()['id']}",
            json={"active": False},
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/budgets?include_inactive=true", headers=_auth(token))
        assert len(resp.json()["items"]) == 1

    async def test_isolated_per_user(self, client: AsyncClient, token: str) -> None:
        await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        stranger = await _register_and_token(client, f"stranger-{uuid4()}@example.com")
        resp = await client.get(f"{API}/budgets", headers=_auth(stranger))
        assert resp.json()["items"] == []


class TestPatch:
    async def test_partial_update(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        resp = await client.patch(
            f"{API}/budgets/{created.json()['id']}",
            json={"amount": "300.00"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["amount"] == "300.00"
        # Untouched fields survive.
        assert body["alert_threshold_pct"] == 80

    async def test_400_on_empty_patch(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        resp = await client.patch(
            f"{API}/budgets/{created.json()['id']}", json={}, headers=_auth(token)
        )
        assert resp.status_code == 400

    async def test_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        stranger = await _register_and_token(client, f"patch-stranger-{uuid4()}@example.com")
        resp = await client.patch(
            f"{API}/budgets/{created.json()['id']}",
            json={"amount": "1.00"},
            headers=_auth(stranger),
        )
        assert resp.status_code == 404


class TestDelete:
    async def test_delete_204(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        budget_id = created.json()["id"]

        resp = await client.delete(f"{API}/budgets/{budget_id}", headers=_auth(token))
        assert resp.status_code == 204

        gone = await client.get(f"{API}/budgets/{budget_id}", headers=_auth(token))
        assert gone.status_code == 404

    async def test_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/budgets", json=_budget_payload(), headers=_auth(token))
        stranger = await _register_and_token(client, f"del-stranger-{uuid4()}@example.com")
        resp = await client.delete(f"{API}/budgets/{created.json()['id']}", headers=_auth(stranger))
        assert resp.status_code == 404


class TestStatus:
    async def test_renders_spend_against_budget(self, client: AsyncClient, token: str) -> None:
        # $200 monthly budget, $30 of in-period spend → ratio 0.15,
        # threshold 80% not triggered.
        await client.post(
            f"{API}/budgets",
            json=_budget_payload(amount="200.00"),
            headers=_auth(token),
        )
        for _ in range(3):
            await client.post(
                f"{API}/expenses",
                json=_expense_payload(amount="10.00", expense_date="2026-04-10"),
                headers=_auth(token),
            )

        resp = await client.get(f"{API}/budgets/status?today=2026-04-15", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["today"] == "2026-04-15"
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["amount"] == "200.00"
        assert item["spent"] == "30.00"
        assert item["remaining"] == "170.00"
        assert abs(item["ratio"] - 0.15) < 1e-6
        assert item["alert_triggered"] is False

    async def test_alert_triggered_when_threshold_crossed(
        self, client: AsyncClient, token: str
    ) -> None:
        # $100 budget, threshold 80%, $90 spend → 90% → alert on.
        await client.post(
            f"{API}/budgets",
            json=_budget_payload(amount="100.00", alert_threshold_pct=80),
            headers=_auth(token),
        )
        await client.post(
            f"{API}/expenses",
            json=_expense_payload(amount="90.00", expense_date="2026-04-10"),
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/budgets/status?today=2026-04-15", headers=_auth(token))
        assert resp.json()["items"][0]["alert_triggered"] is True

    async def test_excludes_inactive_budgets(self, client: AsyncClient, token: str) -> None:
        b = await client.post(
            f"{API}/budgets",
            json=_budget_payload(),
            headers=_auth(token),
        )
        await client.patch(
            f"{API}/budgets/{b.json()['id']}",
            json={"active": False},
            headers=_auth(token),
        )
        resp = await client.get(f"{API}/budgets/status?today=2026-04-15", headers=_auth(token))
        assert resp.json()["items"] == []

    async def test_empty_when_no_budgets(self, client: AsyncClient, token: str) -> None:
        # The status endpoint should short-circuit when there are no
        # active monthly budgets — no second SQL query.
        resp = await client.get(f"{API}/budgets/status", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["items"] == []

    async def test_isolated_per_user(self, client: AsyncClient, token: str) -> None:
        stranger = await _register_and_token(client, f"status-stranger-{uuid4()}@example.com")
        await client.post(
            f"{API}/budgets",
            json=_budget_payload(),
            headers=_auth(stranger),
        )
        # The test user has no budgets — even though stranger has
        # one, our status response stays empty.
        resp = await client.get(f"{API}/budgets/status", headers=_auth(token))
        assert resp.json()["items"] == []


# Silence unused-import warnings from the date-typed payloads.
_ = date

"""Integration tests for /api/v1/expenses.

These hit the real app through the ASGI transport, so they also
exercise the FastAPI dependency wiring and the JWT auth flow.
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
    token = login.json()["access_token"]
    assert isinstance(token, str)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "merchant_name": "Blue Bottle",
        "amount": "4.75",
        "currency": "USD",
        "category": "food_dining",
        "expense_date": "2026-04-20",
        "description": "Morning coffee",
    }
    base.update(overrides)
    return base


@pytest.fixture
async def token(client: AsyncClient) -> str:
    return await _register_and_token(client, "owner@example.com")


class TestCreate:
    async def test_create_201_with_etag(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        assert resp.status_code == 201
        body = resp.json()
        assert body["merchant_name"] == "Blue Bottle"
        assert body["amount"] == "4.75"
        assert body["currency"] == "USD"
        assert body["source"] == "manual"
        assert "id" in body and "user_id" in body
        assert resp.headers.get("etag", "").startswith('W/"')

    async def test_create_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(f"{API}/expenses", json=_payload())
        assert resp.status_code == 401

    async def test_create_rejects_invalid_amount(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(
            f"{API}/expenses",
            json=_payload(amount="-1.00"),
            headers=_auth(token),
        )
        assert resp.status_code == 422


class TestGet:
    async def test_get_returns_owned(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        expense_id = created.json()["id"]

        resp = await client.get(f"{API}/expenses/{expense_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["id"] == expense_id
        assert resp.headers.get("etag", "").startswith('W/"')

    async def test_get_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        # Owner creates a row.
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        expense_id = created.json()["id"]

        stranger_token = await _register_and_token(client, "stranger@example.com")
        resp = await client.get(f"{API}/expenses/{expense_id}", headers=_auth(stranger_token))
        # 404 not 403: existence of the row must not leak to other users.
        assert resp.status_code == 404

    async def test_get_404_for_missing(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/expenses/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404


class TestList:
    async def test_list_returns_owned_only(self, client: AsyncClient, token: str) -> None:
        await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        await client.post(
            f"{API}/expenses",
            json=_payload(merchant_name="Philz"),
            headers=_auth(token),
        )

        stranger_token = await _register_and_token(client, "list-stranger@example.com")
        await client.post(
            f"{API}/expenses",
            json=_payload(merchant_name="Not Mine"),
            headers=_auth(stranger_token),
        )

        resp = await client.get(f"{API}/expenses", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2
        assert {e["merchant_name"] for e in body["items"]} == {"Blue Bottle", "Philz"}
        assert body["next_cursor"] is None

    async def test_list_filters_by_category(self, client: AsyncClient, token: str) -> None:
        await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        await client.post(
            f"{API}/expenses",
            json=_payload(category="groceries", merchant_name="Safeway"),
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/expenses?category=groceries", headers=_auth(token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["merchant_name"] == "Safeway"

    async def test_list_filters_by_merchant_query(self, client: AsyncClient, token: str) -> None:
        await client.post(
            f"{API}/expenses",
            json=_payload(merchant_name="UBER"),
            headers=_auth(token),
        )
        await client.post(
            f"{API}/expenses",
            json=_payload(merchant_name="Lyft", category="transportation"),
            headers=_auth(token),
        )

        resp = await client.get(f"{API}/expenses?merchant=uber", headers=_auth(token))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["merchant_name"] == "UBER"

    async def test_list_paginates(self, client: AsyncClient, token: str) -> None:
        # Five rows on distinct dates so order is deterministic.
        for day in range(5):
            await client.post(
                f"{API}/expenses",
                json=_payload(
                    merchant_name=f"M{day}",
                    expense_date=f"2026-04-0{day + 1}",
                ),
                headers=_auth(token),
            )

        # Page 1 of 2.
        page1 = await client.get(f"{API}/expenses?page_size=2", headers=_auth(token))
        body1 = page1.json()
        assert [e["merchant_name"] for e in body1["items"]] == ["M4", "M3"]
        cursor = body1["next_cursor"]
        assert cursor is not None

        # Page 2 of 2.
        page2 = await client.get(
            f"{API}/expenses?page_size=2&cursor={cursor}", headers=_auth(token)
        )
        body2 = page2.json()
        assert [e["merchant_name"] for e in body2["items"]] == ["M2", "M1"]

        # Final page.
        page3 = await client.get(
            f"{API}/expenses?page_size=2&cursor={body2['next_cursor']}",
            headers=_auth(token),
        )
        body3 = page3.json()
        assert [e["merchant_name"] for e in body3["items"]] == ["M0"]
        assert body3["next_cursor"] is None

    async def test_list_rejects_bad_cursor(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/expenses?cursor=not-a-real-cursor", headers=_auth(token))
        assert resp.status_code == 400

    async def test_list_caps_page_size_via_validation(
        self, client: AsyncClient, token: str
    ) -> None:
        # Router enforces 1 <= page_size <= MAX_PAGE_SIZE at the Query layer.
        resp = await client.get(f"{API}/expenses?page_size=9999", headers=_auth(token))
        assert resp.status_code == 422


class TestPatch:
    async def test_patch_applies_partial_update(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        expense_id = created.json()["id"]

        resp = await client.patch(
            f"{API}/expenses/{expense_id}",
            json={"merchant_name": "Philz"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["merchant_name"] == "Philz"
        # Untouched fields survive.
        assert body["amount"] == "4.75"

    async def test_patch_rejects_empty_body(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        resp = await client.patch(
            f"{API}/expenses/{created.json()['id']}",
            json={},
            headers=_auth(token),
        )
        assert resp.status_code == 400

    async def test_patch_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        stranger_token = await _register_and_token(client, "patch-stranger@example.com")
        resp = await client.patch(
            f"{API}/expenses/{created.json()['id']}",
            json={"merchant_name": "hacked"},
            headers=_auth(stranger_token),
        )
        assert resp.status_code == 404

    async def test_patch_412_on_stale_etag(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        resp = await client.patch(
            f"{API}/expenses/{created.json()['id']}",
            json={"merchant_name": "new"},
            headers=_auth(token) | {"If-Match": 'W/"definitely-not-the-etag"'},
        )
        assert resp.status_code == 412

    async def test_patch_200_with_matching_etag(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        etag = created.headers["etag"]
        resp = await client.patch(
            f"{API}/expenses/{created.json()['id']}",
            json={"merchant_name": "Philz"},
            headers=_auth(token) | {"If-Match": etag},
        )
        assert resp.status_code == 200
        # ETag rotates on success.
        assert resp.headers["etag"] != etag


class TestDelete:
    async def test_delete_204(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        expense_id = created.json()["id"]

        resp = await client.delete(f"{API}/expenses/{expense_id}", headers=_auth(token))
        assert resp.status_code == 204

        # Subsequent GET is 404.
        gone = await client.get(f"{API}/expenses/{expense_id}", headers=_auth(token))
        assert gone.status_code == 404

    async def test_delete_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        stranger_token = await _register_and_token(client, "del-stranger@example.com")
        resp = await client.delete(
            f"{API}/expenses/{created.json()['id']}",
            headers=_auth(stranger_token),
        )
        assert resp.status_code == 404

        # Row still exists for the real owner.
        owner_get = await client.get(f"{API}/expenses/{created.json()['id']}", headers=_auth(token))
        assert owner_get.status_code == 200

    async def test_delete_412_on_stale_etag(self, client: AsyncClient, token: str) -> None:
        created = await client.post(f"{API}/expenses", json=_payload(), headers=_auth(token))
        resp = await client.delete(
            f"{API}/expenses/{created.json()['id']}",
            headers=_auth(token) | {"If-Match": 'W/"nope"'},
        )
        assert resp.status_code == 412


# Silence unused-import warnings from the fixture shadowing.
_ = date

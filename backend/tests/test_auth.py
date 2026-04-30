"""Integration tests for the /auth endpoints.

These hit a real Postgres through the test-harness — the conftest
transaction wrapper rolls back writes between tests.
"""

from __future__ import annotations

from httpx import AsyncClient

API = "/api/v1/auth"


class TestRegister:
    async def test_register_creates_user(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/register",
            json={"email": "alice@example.com", "password": "hunter2hunter2"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["email"] == "alice@example.com"
        assert "id" in body
        assert "password" not in body
        assert "password_hash" not in body

    async def test_register_normalises_email_case(self, client: AsyncClient) -> None:
        await client.post(
            f"{API}/register",
            json={"email": "Bob@Example.com", "password": "hunter2hunter2"},
        )
        conflict = await client.post(
            f"{API}/register",
            json={"email": "bob@example.com", "password": "differentpw123"},
        )
        assert conflict.status_code == 409

    async def test_register_rejects_duplicate_email(self, client: AsyncClient) -> None:
        payload = {"email": "dup@example.com", "password": "hunter2hunter2"}
        first = await client.post(f"{API}/register", json=payload)
        second = await client.post(f"{API}/register", json=payload)
        assert first.status_code == 201
        assert second.status_code == 409

    async def test_register_rejects_weak_password(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/register",
            json={"email": "weak@example.com", "password": "short"},
        )
        assert resp.status_code == 422

    async def test_register_rejects_invalid_email(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/register",
            json={"email": "not-an-email", "password": "hunter2hunter2"},
        )
        assert resp.status_code == 422


class TestLogin:
    async def test_login_returns_access_token(self, client: AsyncClient) -> None:
        await client.post(
            f"{API}/register",
            json={"email": "login@example.com", "password": "hunter2hunter2"},
        )
        resp = await client.post(
            f"{API}/login",
            json={"email": "login@example.com", "password": "hunter2hunter2"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0
        assert len(body["access_token"]) > 0

    async def test_login_rejects_wrong_password(self, client: AsyncClient) -> None:
        await client.post(
            f"{API}/register",
            json={"email": "wrongpw@example.com", "password": "hunter2hunter2"},
        )
        resp = await client.post(
            f"{API}/login",
            json={"email": "wrongpw@example.com", "password": "notthepassword"},
        )
        assert resp.status_code == 401

    async def test_login_rejects_unknown_email(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/login",
            json={"email": "ghost@example.com", "password": "hunter2hunter2"},
        )
        assert resp.status_code == 401


class TestMe:
    async def test_me_returns_current_user(self, client: AsyncClient) -> None:
        await client.post(
            f"{API}/register",
            json={"email": "me@example.com", "password": "hunter2hunter2"},
        )
        login = await client.post(
            f"{API}/login",
            json={"email": "me@example.com", "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]

        resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["email"] == "me@example.com"

    async def test_me_requires_authentication(self, client: AsyncClient) -> None:
        resp = await client.get(f"{API}/me")
        assert resp.status_code == 401

    async def test_me_rejects_bogus_token(self, client: AsyncClient) -> None:
        resp = await client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-real-token"})
        assert resp.status_code == 401


class TestInboxToken:
    """Phase 5.5 forward-to-email surface.

    Every user gets a 128-bit hex token at signup and a derived
    forward-to-email address rendered against the configured
    ``inbox_email_domain``. These tests lock in that contract.
    """

    async def test_register_response_carries_inbox_address(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/register",
            json={"email": "mailbox@example.com", "password": "hunter2hunter2"},
        )
        assert resp.status_code == 201
        body = resp.json()
        # 128 bits = 32 hex chars.
        assert len(body["inbox_token"]) == 32
        assert all(c in "0123456789abcdef" for c in body["inbox_token"])
        # Address is derived: ``receipts+<token>@<domain>``.
        assert body["inbox_address"].startswith("receipts+")
        assert body["inbox_address"].endswith("@inbox.spendlens.local")
        assert body["inbox_token"] in body["inbox_address"]

    async def test_me_returns_same_token_as_register(self, client: AsyncClient) -> None:
        registered = await client.post(
            f"{API}/register",
            json={"email": "stable@example.com", "password": "hunter2hunter2"},
        )
        login = await client.post(
            f"{API}/login",
            json={"email": "stable@example.com", "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]
        me = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {token}"})

        # Token is immutable across the session — the address the
        # user types into Gmail filters has to keep working.
        assert me.json()["inbox_token"] == registered.json()["inbox_token"]

    async def test_each_user_gets_a_distinct_token(self, client: AsyncClient) -> None:
        a = await client.post(
            f"{API}/register",
            json={"email": "a@example.com", "password": "hunter2hunter2"},
        )
        b = await client.post(
            f"{API}/register",
            json={"email": "b@example.com", "password": "hunter2hunter2"},
        )
        assert a.json()["inbox_token"] != b.json()["inbox_token"]

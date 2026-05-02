"""Tests for the Gmail OAuth flow surface (Phase 5.6 PR B).

Three layers:

* **State envelope** — pure function, exercised directly. Locks in
  the timing-safe compare, the 10-minute window, and the malformed-
  envelope rejection paths.
* **Service layer** — ``exchange_code`` and ``revoke_refresh_token``
  with an :class:`httpx.MockTransport`–backed client that simulates
  Google. No network.
* **Endpoints** — POST through the ASGI transport, monkeypatching
  the endpoint module's HTTP-client builder so the callback uses
  the same mock transport. Verifies the connections row lands, the
  cross-tenant 404, the 503 when not configured, and the redirect
  outcomes.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints import integrations_gmail as endpoint_mod
from app.core.config import get_settings
from app.core.secret_box import decrypt_secret
from app.models.gmail_connection import GmailConnection
from app.services import gmail_oauth_service
from app.services.gmail_oauth_service import (
    GmailNotConfiguredError,
    OAuthStateError,
    TokenExchangeError,
    _sign_state,
    _verify_state,
    build_consent_url,
    exchange_code,
    revoke_refresh_token,
)
from app.services.user_service import create_user

API = "/api/v1"

_TEST_CLIENT_ID = "test-client.apps.googleusercontent.com"
_TEST_CLIENT_SECRET = "test-client-secret"
_TEST_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/gmail/callback"


# ----- env wiring --------------------------------------------------------


@pytest.fixture
def configured_oauth(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Pin OAuth client id/secret + a fresh Fernet key for each test."""
    fernet_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", _TEST_CLIENT_ID)
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", _TEST_CLIENT_SECRET)
    monkeypatch.setenv("GMAIL_OAUTH_REDIRECT_URI", _TEST_REDIRECT_URI)
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", fernet_key)
    get_settings.cache_clear()
    try:
        yield fernet_key
    finally:
        get_settings.cache_clear()


# ----- HTTP mocking helpers ----------------------------------------------


def _mock_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """Build a real ``AsyncClient`` whose transport is a single handler."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _google_happy_handler(
    *,
    refresh_token: str = "refresh-xyz",
    access_token: str = "access-xyz",
    google_email: str = "alice@gmail.com",
) -> Callable[[httpx.Request], httpx.Response]:
    """Handler that simulates a successful token + userinfo exchange."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "refresh_token": refresh_token,
                    "access_token": access_token,
                    "expires_in": 3600,
                    "token_type": "Bearer",
                    "scope": gmail_oauth_service.GMAIL_READONLY_SCOPE,
                },
            )
        if request.url.path == "/oauth2/v3/userinfo":
            assert request.method == "GET"
            assert request.headers["authorization"] == f"Bearer {access_token}"
            return httpx.Response(200, json={"email": google_email, "email_verified": True})
        if request.url.path == "/revoke":
            return httpx.Response(200)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return handle


# ----- 1. State signing ---------------------------------------------------


class TestStateSigning:
    def test_round_trip(self) -> None:
        user_id = uuid4()
        secret = "x" * 32
        state = _sign_state(user_id, nonce="abc", issued_at=1_700_000_000, secret=secret)
        # Verify treats the issued_at as in-window relative to a
        # ``now`` we control for determinism.
        assert _verify_state(state, secret=secret, now=1_700_000_001) == user_id

    def test_malformed_envelope_raises(self) -> None:
        with pytest.raises(OAuthStateError, match="malformed state"):
            _verify_state("not-enough-dots", secret="x" * 32)

    def test_signature_mismatch_raises(self) -> None:
        user_id = uuid4()
        state = _sign_state(user_id, nonce="abc", issued_at=1_700_000_000, secret="key-a" * 8)
        with pytest.raises(OAuthStateError, match="mismatch"):
            _verify_state(state, secret="key-b" * 8, now=1_700_000_001)

    def test_expired_state_raises(self) -> None:
        user_id = uuid4()
        secret = "x" * 32
        state = _sign_state(user_id, nonce="abc", issued_at=1_700_000_000, secret=secret)
        # 11 minutes later — outside the 10-minute window.
        with pytest.raises(OAuthStateError, match="expired"):
            _verify_state(state, secret=secret, now=1_700_000_000 + 11 * 60)

    def test_future_dated_state_raises(self) -> None:
        user_id = uuid4()
        secret = "x" * 32
        # Issued 11 minutes from now — clock skew or forgery.
        state = _sign_state(user_id, nonce="abc", issued_at=1_700_000_000 + 11 * 60, secret=secret)
        with pytest.raises(OAuthStateError, match="future"):
            _verify_state(state, secret=secret, now=1_700_000_000)

    def test_malformed_subject_raises(self) -> None:
        secret = "x" * 32
        # Hand-craft a state with a non-UUID subject. The HMAC
        # itself is valid; the parser of the inner field fails.
        import hmac as _hmac
        from hashlib import sha256 as _sha256

        payload = "not-a-uuid.nonce.1700000000"
        sig = _hmac.new(secret.encode(), payload.encode(), _sha256).hexdigest()
        bad = f"{payload}.{sig}"
        with pytest.raises(OAuthStateError, match="subject"):
            _verify_state(bad, secret=secret, now=1_700_000_001)


# ----- 2. Consent URL -----------------------------------------------------


class TestConsentURL:
    def test_url_includes_required_params(self, configured_oauth: str) -> None:
        url = build_consent_url(uuid4())
        # Don't pin the full URL — Google can re-order, and the
        # state nonce is random. Spot-check the params we care about.
        assert url.startswith(gmail_oauth_service.GOOGLE_AUTH_URL + "?")
        assert "access_type=offline" in url
        assert "prompt=consent" in url
        assert f"client_id={_TEST_CLIENT_ID}" in url
        assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in url
        assert "state=" in url

    def test_raises_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(GmailNotConfiguredError):
                build_consent_url(uuid4())
        finally:
            get_settings.cache_clear()


# ----- 3. exchange_code service path --------------------------------------


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_happy_path_returns_user_id_and_tokens(self, configured_oauth: str) -> None:
        user_id = uuid4()
        settings = get_settings()
        state = _sign_state(
            user_id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )

        async with _mock_client(_google_happy_handler()) as client:
            returned_id, tokens = await exchange_code(code="auth-code", state=state, client=client)
        assert returned_id == user_id
        assert tokens.refresh_token == "refresh-xyz"
        assert tokens.access_token == "access-xyz"
        assert tokens.google_email == "alice@gmail.com"

    @pytest.mark.asyncio
    async def test_bad_state_raises_before_calling_google(self, configured_oauth: str) -> None:
        # ``unreachable_handler`` would fail the test if the service
        # called Google with a bad state.
        def unreachable_handler(_: httpx.Request) -> httpx.Response:
            raise AssertionError("Google must not be called when state is invalid")

        async with _mock_client(unreachable_handler) as client:
            with pytest.raises(OAuthStateError):
                await exchange_code(code="auth-code", state="garbage.envelope", client=client)

    @pytest.mark.asyncio
    async def test_token_endpoint_4xx_raises_token_exchange_error(
        self, configured_oauth: str
    ) -> None:
        user_id = uuid4()
        settings = get_settings()
        state = _sign_state(
            user_id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )

        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        async with _mock_client(handle) as client:
            with pytest.raises(TokenExchangeError, match="HTTP 400"):
                await exchange_code(code="bad", state=state, client=client)

    @pytest.mark.asyncio
    async def test_token_response_missing_refresh_token_raises(self, configured_oauth: str) -> None:
        user_id = uuid4()
        settings = get_settings()
        state = _sign_state(
            user_id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )

        def handle(request: httpx.Request) -> httpx.Response:
            # Access token only — Google sometimes does this on a
            # non-consenting re-grant. We refuse rather than store
            # half a connection.
            return httpx.Response(200, json={"access_token": "a", "expires_in": 3600})

        async with _mock_client(handle) as client:
            with pytest.raises(TokenExchangeError, match="missing"):
                await exchange_code(code="c", state=state, client=client)

    @pytest.mark.asyncio
    async def test_userinfo_failure_raises(self, configured_oauth: str) -> None:
        user_id = uuid4()
        settings = get_settings()
        state = _sign_state(
            user_id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"refresh_token": "r", "access_token": "a"})
            return httpx.Response(401, json={"error": "invalid_token"})

        async with _mock_client(handle) as client:
            with pytest.raises(TokenExchangeError, match="userinfo"):
                await exchange_code(code="c", state=state, client=client)

    @pytest.mark.asyncio
    async def test_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
        get_settings.cache_clear()
        try:
            async with _mock_client(_google_happy_handler()) as client:
                with pytest.raises(GmailNotConfiguredError):
                    await exchange_code(code="c", state="any", client=client)
        finally:
            get_settings.cache_clear()


# ----- 4. revoke_refresh_token --------------------------------------------


class TestRevoke:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self) -> None:
        async with _mock_client(lambda _: httpx.Response(200)) as client:
            assert await revoke_refresh_token(refresh_token="r", client=client) is True

    @pytest.mark.asyncio
    async def test_returns_true_on_400_already_revoked(self) -> None:
        # Google's docs: 400 means "token already revoked or never
        # valid". From our perspective: same outcome as success.
        async with _mock_client(
            lambda _: httpx.Response(400, json={"error": "invalid_token"})
        ) as client:
            assert await revoke_refresh_token(refresh_token="r", client=client) is True

    @pytest.mark.asyncio
    async def test_returns_false_on_5xx(self) -> None:
        async with _mock_client(lambda _: httpx.Response(503)) as client:
            assert await revoke_refresh_token(refresh_token="r", client=client) is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self) -> None:
        def boom(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network down")

        async with _mock_client(boom) as client:
            assert await revoke_refresh_token(refresh_token="r", client=client) is False


# ----- 5. Endpoints -------------------------------------------------------


async def _register_and_token(client: AsyncClient, email: str) -> tuple[UUID, str]:
    reg = await client.post(
        f"{API}/auth/register", json={"email": email, "password": "hunter2hunter2"}
    )
    user_id = UUID(reg.json()["id"])
    login = await client.post(
        f"{API}/auth/login", json={"email": email, "password": "hunter2hunter2"}
    )
    return user_id, str(login.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patch_endpoint_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Swap the endpoint module's HTTP-client builder for a mocked one."""

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(endpoint_mod, "_build_http_client", factory)


class TestConnectEndpoint:
    @pytest.mark.asyncio
    async def test_returns_consent_url(self, client: AsyncClient, configured_oauth: str) -> None:
        _, token = await _register_and_token(client, f"connect-{uuid4()}@example.com")
        resp = await client.get(f"{API}/integrations/gmail/connect", headers=_auth(token))
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert url.startswith(gmail_oauth_service.GOOGLE_AUTH_URL)
        assert "state=" in url

    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient, configured_oauth: str) -> None:
        resp = await client.get(f"{API}/integrations/gmail/connect")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_503_when_not_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
        get_settings.cache_clear()
        try:
            _, token = await _register_and_token(client, f"unconfig-{uuid4()}@example.com")
            resp = await client.get(f"{API}/integrations/gmail/connect", headers=_auth(token))
            assert resp.status_code == 503
        finally:
            get_settings.cache_clear()


class TestCallbackEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_persists_connection_and_redirects(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await create_user(
            db_session, email=f"cb-{uuid4()}@example.com", password="hunter2hunter2"
        )
        settings = get_settings()
        state = _sign_state(
            user.id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )
        _patch_endpoint_client(monkeypatch, _google_happy_handler(google_email="alice@gmail.com"))

        resp = await client.get(
            f"{API}/integrations/gmail/callback",
            params={"code": "auth-code", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/receipts?gmail=connected"

        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].google_email == "alice@gmail.com"
        # Round-trip the encrypted token to prove the foundation is
        # actually in use — not just a string write.
        assert decrypt_secret(rows[0].encrypted_refresh_token) == "refresh-xyz"

    @pytest.mark.asyncio
    async def test_re_consent_replaces_token_for_same_account(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await create_user(
            db_session, email=f"reup-{uuid4()}@example.com", password="hunter2hunter2"
        )
        settings = get_settings()

        # First grant.
        state1 = _sign_state(
            user.id, nonce="a", issued_at=int(time.time()), secret=settings.jwt_secret
        )
        _patch_endpoint_client(
            monkeypatch,
            _google_happy_handler(refresh_token="r-first", google_email="b@gmail.com"),
        )
        await client.get(
            f"{API}/integrations/gmail/callback",
            params={"code": "c1", "state": state1},
            follow_redirects=False,
        )

        # Re-consent for the same Google account; the row should be
        # updated, not duplicated.
        state2 = _sign_state(
            user.id, nonce="b", issued_at=int(time.time()), secret=settings.jwt_secret
        )
        _patch_endpoint_client(
            monkeypatch,
            _google_happy_handler(refresh_token="r-second", google_email="b@gmail.com"),
        )
        await client.get(
            f"{API}/integrations/gmail/callback",
            params={"code": "c2", "state": state2},
            follow_redirects=False,
        )

        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert decrypt_secret(rows[0].encrypted_refresh_token) == "r-second"

    @pytest.mark.asyncio
    async def test_bad_state_redirects_to_error(
        self,
        client: AsyncClient,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Google must not be reached.
        def unreachable(_: httpx.Request) -> httpx.Response:
            raise AssertionError("Google must not be called on bad state")

        _patch_endpoint_client(monkeypatch, unreachable)
        resp = await client.get(
            f"{API}/integrations/gmail/callback",
            params={"code": "x", "state": "garbage"},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/receipts?gmail=error")
        assert "bad_state" in resp.headers["location"]

    @pytest.mark.asyncio
    async def test_exchange_failure_redirects_to_error(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await create_user(
            db_session, email=f"fail-{uuid4()}@example.com", password="hunter2hunter2"
        )
        settings = get_settings()
        state = _sign_state(
            user.id, nonce="n", issued_at=int(time.time()), secret=settings.jwt_secret
        )

        _patch_endpoint_client(
            monkeypatch,
            lambda _: httpx.Response(400, json={"error": "invalid_grant"}),
        )
        resp = await client.get(
            f"{API}/integrations/gmail/callback",
            params={"code": "x", "state": state},
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "exchange_failed" in resp.headers["location"]

        # No row should have been written.
        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_not_configured_redirects_to_error(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_ID", raising=False)
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
        get_settings.cache_clear()
        try:
            resp = await client.get(
                f"{API}/integrations/gmail/callback",
                params={"code": "x", "state": "y"},
                follow_redirects=False,
            )
            assert resp.status_code == 302
            assert "not_configured" in resp.headers["location"]
        finally:
            get_settings.cache_clear()


class TestListEndpoint:
    @pytest.mark.asyncio
    async def test_lists_only_owners_connections(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Two users, each connects a different Gmail account.
        alice = await create_user(
            db_session, email=f"alice-{uuid4()}@example.com", password="hunter2hunter2"
        )
        bob = await create_user(
            db_session, email=f"bob-{uuid4()}@example.com", password="hunter2hunter2"
        )

        from app.services.gmail_connection_service import upsert_connection

        await upsert_connection(
            db_session, user_id=alice.id, google_email="a@gmail.com", refresh_token="ra"
        )
        await upsert_connection(
            db_session, user_id=bob.id, google_email="b@gmail.com", refresh_token="rb"
        )

        # Log in as Alice; she should see exactly one row.
        login = await client.post(
            f"{API}/auth/login",
            json={"email": alice.email, "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]

        resp = await client.get(f"{API}/integrations/gmail", headers=_auth(token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["google_email"] == "a@gmail.com"
        # No token field on the response — anywhere.
        assert "encrypted_refresh_token" not in items[0]
        assert "refresh_token" not in items[0]

    @pytest.mark.asyncio
    async def test_requires_auth(self, client: AsyncClient, configured_oauth: str) -> None:
        resp = await client.get(f"{API}/integrations/gmail")
        assert resp.status_code == 401


class TestDeleteEndpoint:
    @pytest.mark.asyncio
    async def test_deletes_and_revokes(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await create_user(
            db_session, email=f"del-{uuid4()}@example.com", password="hunter2hunter2"
        )
        from app.services.gmail_connection_service import upsert_connection

        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="a@gmail.com", refresh_token="r"
        )

        revoke_calls: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/revoke"
            # Ensure the plaintext refresh token was decrypted before
            # it left the process — Google needs the original, not
            # the ciphertext.
            assert b"token=r" in request.content
            revoke_calls.append("ok")
            return httpx.Response(200)

        _patch_endpoint_client(monkeypatch, handle)

        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]

        resp = await client.delete(
            f"{API}/integrations/gmail/{connection.id}", headers=_auth(token)
        )
        assert resp.status_code == 204
        assert revoke_calls == ["ok"]

        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_404_on_missing(
        self,
        client: AsyncClient,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, token = await _register_and_token(client, f"miss-{uuid4()}@example.com")
        resp = await client.delete(f"{API}/integrations/gmail/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_404_on_cross_tenant(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Alice has a connection. Bob tries to delete it by id.
        alice = await create_user(
            db_session, email=f"alice2-{uuid4()}@example.com", password="hunter2hunter2"
        )
        from app.services.gmail_connection_service import upsert_connection

        alice_conn = await upsert_connection(
            db_session, user_id=alice.id, google_email="a@gmail.com", refresh_token="r"
        )

        _, bob_token = await _register_and_token(client, f"bob2-{uuid4()}@example.com")
        resp = await client.delete(
            f"{API}/integrations/gmail/{alice_conn.id}", headers=_auth(bob_token)
        )
        # 404 — not 403 — so Bob can't probe whether the id exists.
        assert resp.status_code == 404

        # Alice's row is untouched.
        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == alice.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_deletes_even_if_remote_revoke_fails(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_oauth: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Local delete is the source of truth. If Google's revoke
        # endpoint is down, we still tear the row out.
        user = await create_user(
            db_session, email=f"localdel-{uuid4()}@example.com", password="hunter2hunter2"
        )
        from app.services.gmail_connection_service import upsert_connection

        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="a@gmail.com", refresh_token="r"
        )

        _patch_endpoint_client(monkeypatch, lambda _: httpx.Response(503))

        login = await client.post(
            f"{API}/auth/login",
            json={"email": user.email, "password": "hunter2hunter2"},
        )
        token = login.json()["access_token"]

        resp = await client.delete(
            f"{API}/integrations/gmail/{connection.id}", headers=_auth(token)
        )
        assert resp.status_code == 204
        rows = (
            (
                await db_session.execute(
                    select(GmailConnection).where(GmailConnection.user_id == user.id)
                )
            )
            .scalars()
            .all()
        )
        assert rows == []


# Mark every async test method up front so pytest-asyncio's auto mode
# picks them up. Some test methods are class-scoped synchronous
# helpers — they don't need decoration. The ``Any`` re-export below
# silences a strict-mypy unused-import on this dev-only module.
_ = Any

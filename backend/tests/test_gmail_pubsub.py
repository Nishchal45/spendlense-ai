"""Tests for the Pub/Sub push verification + decode (Phase 5.6 PR C).

Covers:

* JWT verification — every failure arm of :func:`verify_push_request`
  using a stub verifier so the suite never hits Google's cert
  endpoint.
* Pub/Sub envelope decoding — happy path, malformed JSON, wrong
  shape, bad base64, missing fields.
* Endpoint integration — the push route through the ASGI transport
  with the verifier and the Celery task both stubbed out, asserting
  that a verified push lands in the right (mock) queue and that
  failure paths return the documented status codes.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services import gmail_pubsub_service
from app.services.gmail_connection_service import upsert_connection
from app.services.gmail_pubsub_service import (
    PushNotConfiguredError,
    PushVerificationError,
    verify_push_request,
)
from app.services.user_service import create_user

API = "/api/v1"

_TEST_AUDIENCE = "https://api.spendlens.test/api/v1/integrations/gmail/push"
_TEST_SERVICE_ACCOUNT = "pubsub-push@example.iam.gserviceaccount.com"


# ----- env wiring --------------------------------------------------------


@pytest.fixture
def configured_pubsub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the audience + service-account env vars."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("GMAIL_PUBSUB_AUDIENCE", _TEST_AUDIENCE)
    monkeypatch.setenv("GMAIL_PUBSUB_SERVICE_ACCOUNT", _TEST_SERVICE_ACCOUNT)
    # Encryption key + OAuth client are required by the connection
    # service that runs inside the push handler. Without them the
    # endpoint would 503 before reaching the verifier.
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "test-client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "test-client-secret")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


# ----- helpers -----------------------------------------------------------


def _make_envelope(
    *,
    email_address: str,
    history_id: int | str = 9876,
    message_id: str = "pm-1",
) -> bytes:
    """Build a real Pub/Sub push envelope, base64-encoded as Google sends it."""
    inner = json.dumps({"emailAddress": email_address, "historyId": history_id}).encode()
    return json.dumps(
        {
            "message": {
                "data": base64.b64encode(inner).decode("ascii"),
                "messageId": message_id,
                "publishTime": "2026-05-02T10:00:00Z",
            },
            "subscription": "projects/p/subscriptions/s",
        }
    ).encode()


def _claims(
    *,
    aud: str = _TEST_AUDIENCE,
    iss: str = "https://accounts.google.com",
    email: str = _TEST_SERVICE_ACCOUNT,
) -> dict[str, Any]:
    return {"aud": aud, "iss": iss, "email": email, "exp": 9999999999, "iat": 0}


def _stub_verifier_returning(claims: dict[str, Any]):
    def verify(_token: str, _request: Any, _audience: str) -> dict[str, Any]:
        return claims

    return verify


# ----- 1. verify_push_request -------------------------------------------


class TestVerifyPushRequest:
    def test_happy_path(self, configured_pubsub: None) -> None:
        body = _make_envelope(email_address="alice@gmail.com", history_id=1234)
        msg = verify_push_request(
            authorization_header="Bearer fake.jwt.token",
            raw_body=body,
            verifier=_stub_verifier_returning(_claims()),
        )
        assert msg.email_address == "alice@gmail.com"
        # Numeric historyId is coerced to a string.
        assert msg.history_id == "1234"
        assert msg.pubsub_message_id == "pm-1"

    def test_missing_authorization_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="missing Authorization"):
            verify_push_request(
                authorization_header=None,
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_malformed_bearer_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="malformed"):
            verify_push_request(
                authorization_header="Token abc",
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_empty_token_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="empty"):
            verify_push_request(
                authorization_header="Bearer ",
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_jwt_signature_failure_raises(self, configured_pubsub: None) -> None:
        def boom(_token: str, _req: Any, _aud: str) -> dict[str, Any]:
            raise ValueError("Invalid signature")

        with pytest.raises(PushVerificationError, match="verification failed"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=boom,
            )

    def test_wrong_issuer_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="unexpected issuer"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=_stub_verifier_returning(_claims(iss="https://evil.example")),
            )

    def test_wrong_service_account_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="service account"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=_make_envelope(email_address="a@gmail.com"),
                verifier=_stub_verifier_returning(
                    _claims(email="someone-else@example.iam.gserviceaccount.com")
                ),
            )

    def test_not_configured_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GMAIL_PUBSUB_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUBSUB_SERVICE_ACCOUNT", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(PushNotConfiguredError):
                verify_push_request(
                    authorization_header="Bearer x",
                    raw_body=_make_envelope(email_address="a@gmail.com"),
                    verifier=_stub_verifier_returning(_claims()),
                )
        finally:
            get_settings.cache_clear()


# ----- 2. body decoding (verifier already passed) ------------------------


class TestBodyDecoding:
    def test_invalid_json_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="not valid JSON"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=b"not json",
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_missing_message_object_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="message"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=json.dumps({"subscription": "p/s"}).encode(),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_missing_data_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="'data'"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=json.dumps({"message": {"messageId": "x"}}).encode(),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_invalid_base64_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="base64"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=json.dumps({"message": {"data": "not!base64", "messageId": "x"}}).encode(),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_inner_not_json_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="data is not valid JSON"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=json.dumps(
                    {
                        "message": {
                            "data": base64.b64encode(b"not json").decode(),
                            "messageId": "x",
                        }
                    }
                ).encode(),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_missing_email_address_raises(self, configured_pubsub: None) -> None:
        with pytest.raises(PushVerificationError, match="emailAddress"):
            verify_push_request(
                authorization_header="Bearer x",
                raw_body=json.dumps(
                    {
                        "message": {
                            "data": base64.b64encode(b'{"historyId":1}').decode(),
                            "messageId": "x",
                        }
                    }
                ).encode(),
                verifier=_stub_verifier_returning(_claims()),
            )

    def test_history_id_as_string_is_accepted(self, configured_pubsub: None) -> None:
        body = _make_envelope(email_address="a@gmail.com", history_id="9999999999999999999")
        msg = verify_push_request(
            authorization_header="Bearer x",
            raw_body=body,
            verifier=_stub_verifier_returning(_claims()),
        )
        assert msg.history_id == "9999999999999999999"


# ----- 3. push endpoint --------------------------------------------------


class TestPushEndpoint:
    @pytest.mark.asyncio
    async def test_happy_path_enqueues_task_per_connection(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        configured_pubsub: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = await create_user(
            db_session, email=f"push-{uuid4()}@example.com", password="hunter2hunter2"
        )
        await upsert_connection(
            db_session, user_id=user.id, google_email="alice@gmail.com", refresh_token="r"
        )

        # Stub the verifier so the endpoint accepts our fake JWT.
        monkeypatch.setattr(
            gmail_pubsub_service,
            "_default_verifier",
            lambda: _stub_verifier_returning(_claims()),
        )

        # Capture the task enqueue without a real Celery dispatch.
        enqueued: list[tuple[str, str]] = []

        class _Stub:
            @staticmethod
            def delay(connection_id: str, history_id: str) -> None:
                enqueued.append((connection_id, history_id))

        # The endpoint imports the task lazily; patch the module
        # attribute so the lazy import lands on our stub.
        from app.tasks import gmail_history_sync as task_mod

        monkeypatch.setattr(task_mod, "gmail_history_sync", _Stub)

        body = _make_envelope(email_address="alice@gmail.com", history_id=42)
        resp = await client.post(
            f"{API}/integrations/gmail/push",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer fake.jwt.token",
            },
        )
        assert resp.status_code == 204, resp.text
        assert len(enqueued) == 1
        assert enqueued[0][1] == "42"

    @pytest.mark.asyncio
    async def test_no_matching_connection_returns_204(
        self,
        client: AsyncClient,
        configured_pubsub: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No connection rows for this email — verifier passes, lookup
        # is empty, response is 204 (so Pub/Sub stops retrying).
        monkeypatch.setattr(
            gmail_pubsub_service,
            "_default_verifier",
            lambda: _stub_verifier_returning(_claims()),
        )

        body = _make_envelope(email_address="ghost@gmail.com")
        resp = await client.post(
            f"{API}/integrations/gmail/push",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer fake.jwt.token",
            },
        )
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_401_on_jwt_failure(
        self,
        client: AsyncClient,
        configured_pubsub: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail(_t: str, _r: Any, _a: str) -> dict[str, Any]:
            raise ValueError("bad jwt")

        monkeypatch.setattr(gmail_pubsub_service, "_default_verifier", lambda: fail)

        body = _make_envelope(email_address="a@gmail.com")
        resp = await client.post(
            f"{API}/integrations/gmail/push",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer fake.jwt.token",
            },
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_503_when_not_configured(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("GMAIL_PUBSUB_AUDIENCE", raising=False)
        monkeypatch.delenv("GMAIL_PUBSUB_SERVICE_ACCOUNT", raising=False)
        get_settings.cache_clear()
        try:
            body = _make_envelope(email_address="a@gmail.com")
            resp = await client.post(
                f"{API}/integrations/gmail/push",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer fake.jwt.token",
                },
            )
            assert resp.status_code == 503
        finally:
            get_settings.cache_clear()

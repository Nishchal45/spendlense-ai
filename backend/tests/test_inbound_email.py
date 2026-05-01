"""Integration tests for inbound-email ingestion.

Three layers of coverage:

* **Signature verification** — pure function, exercised directly.
  Locks in the timing-safe compare, the timestamp window, and the
  malformed-header rejection paths.
* **Token resolution** — the regex + DB lookup that maps a
  ``receipts+<token>@...`` address back to a user.
* **End-to-end webhook** — POST through the ASGI transport with a
  signed body, verify a Receipt row lands in the DB, verify dedup
  on a re-delivery, verify all the failure-mode response codes.
"""

from __future__ import annotations

import base64
import hmac
import json
from hashlib import sha256
from time import time
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.receipt import Receipt
from app.services.inbound_email_service import (
    InvalidSignatureError,
    UnknownInboxTokenError,
    resolve_user_by_to_address,
    verify_webhook_signature,
)
from app.services.user_service import create_user

API = "/api/v1"

# Tests pin the secret rather than read it from the env so they
# don't depend on conftest setup. The endpoint reads from settings
# via monkeypatch in each test that hits the wire.
_TEST_SECRET = "test-inbound-email-secret-not-real"


def _sign(body: bytes, *, secret: str = _TEST_SECRET, ts: int | None = None) -> str:
    """Mint the ``X-SpendLens-Signature`` header for a body."""
    timestamp = ts if ts is not None else int(time())
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + body,
        sha256,
    ).hexdigest()
    return f"t={timestamp},v1={sig}"


# 1x1 transparent PNG (real magic bytes, the rest is enough to
# satisfy the backend's MIME sniff). Same trick the receipts tests
# already use.
PNG_PIXEL = b"\x89PNG\r\n\x1a\n" + b"png-body" * 4


def _payload(*, message_id: str, to: str) -> dict[str, object]:
    return {
        "message_id": message_id,
        "to": to,
        "sender": "alice@example.com",
        "subject": "Your Uber receipt",
        "body_plain": "See attached.",
        "attachments": [
            {
                "filename": "receipt.png",
                "content_type": "image/png",
                "content_base64": base64.b64encode(PNG_PIXEL).decode(),
            }
        ],
    }


# ----- signature verification ---------------------------------------------


class TestSignatureVerification:
    def test_valid_signature_passes(self) -> None:
        body = b'{"hello": "world"}'
        header = _sign(body)
        # No exception = pass.
        verify_webhook_signature(raw_body=body, signature_header=header, secret=_TEST_SECRET)

    def test_missing_header_raises(self) -> None:
        with pytest.raises(InvalidSignatureError, match="missing"):
            verify_webhook_signature(raw_body=b"{}", signature_header=None, secret=_TEST_SECRET)

    def test_malformed_header_raises(self) -> None:
        with pytest.raises(InvalidSignatureError, match="malformed"):
            verify_webhook_signature(
                raw_body=b"{}",
                signature_header="not-a-real-header",
                secret=_TEST_SECRET,
            )

    def test_stale_timestamp_raises(self) -> None:
        # Sign with a timestamp 10 minutes ago — outside the 5 min
        # replay window.
        old_ts = int(time()) - 600
        body = b"{}"
        header = _sign(body, ts=old_ts)
        with pytest.raises(InvalidSignatureError, match="timestamp"):
            verify_webhook_signature(raw_body=body, signature_header=header, secret=_TEST_SECRET)

    def test_signature_mismatch_raises(self) -> None:
        body = b'{"hello": "world"}'
        # Sign with a different secret — the verifier should
        # reject without leaking which step failed.
        header = _sign(body, secret="different-secret")
        with pytest.raises(InvalidSignatureError, match="mismatch"):
            verify_webhook_signature(raw_body=body, signature_header=header, secret=_TEST_SECRET)

    def test_body_tampering_raises(self) -> None:
        body = b'{"hello": "world"}'
        header = _sign(body)
        # Caller swapped the body after signing — different body,
        # same header, must reject.
        with pytest.raises(InvalidSignatureError, match="mismatch"):
            verify_webhook_signature(
                raw_body=b'{"hello": "tampered"}',
                signature_header=header,
                secret=_TEST_SECRET,
            )


# ----- token resolution ---------------------------------------------------


class TestTokenResolution:
    async def test_resolves_user_by_address(self, db_session: AsyncSession) -> None:
        user = await create_user(
            db_session, email=f"resolve-{uuid4()}@example.com", password="hunter2hunter2"
        )
        address = f"receipts+{user.inbox_token}@inbox.spendlens.local"

        resolved = await resolve_user_by_to_address(db_session, to_address=address)
        assert resolved.id == user.id

    async def test_unknown_token_raises(self, db_session: AsyncSession) -> None:
        # Valid shape, but no user has this token.
        address = "receipts+" + ("0" * 32) + "@inbox.spendlens.local"
        with pytest.raises(UnknownInboxTokenError):
            await resolve_user_by_to_address(db_session, to_address=address)

    async def test_malformed_address_raises(self, db_session: AsyncSession) -> None:
        with pytest.raises(UnknownInboxTokenError):
            await resolve_user_by_to_address(db_session, to_address="someone@gmail.com")


# ----- end-to-end webhook -------------------------------------------------


class TestInboundEmailEndpoint:
    async def test_503_when_secret_not_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Default conftest doesn't set ``INBOUND_EMAIL_SECRET`` so
        # ``settings.inbound_email_secret`` is None — the route
        # returns 503 to make the misconfiguration visible.
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(f"{API}/inbound/email", json={"hello": "world"})
            assert resp.status_code == 503
        finally:
            get_settings.cache_clear()

    async def test_401_on_bad_signature(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("INBOUND_EMAIL_SECRET", _TEST_SECRET)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            resp = await client.post(
                f"{API}/inbound/email",
                json={"hello": "world"},
                headers={"X-SpendLens-Signature": "garbage"},
            )
            assert resp.status_code == 401
        finally:
            get_settings.cache_clear()

    async def test_404_on_unknown_recipient(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("INBOUND_EMAIL_SECRET", _TEST_SECRET)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            payload = _payload(
                message_id="msg-unknown-1",
                to=f"receipts+{'0' * 32}@inbox.spendlens.local",
            )
            body = json.dumps(payload).encode()
            resp = await client.post(
                f"{API}/inbound/email",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SpendLens-Signature": _sign(body),
                },
            )
            assert resp.status_code == 404
        finally:
            get_settings.cache_clear()

    async def test_creates_receipt_on_happy_path(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("INBOUND_EMAIL_SECRET", _TEST_SECRET)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            user = await create_user(
                db_session,
                email=f"inbound-{uuid4()}@example.com",
                password="hunter2hunter2",
            )

            payload = _payload(
                message_id=f"msg-{uuid4()}",
                to=f"receipts+{user.inbox_token}@inbox.spendlens.local",
            )
            body = json.dumps(payload).encode()
            resp = await client.post(
                f"{API}/inbound/email",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SpendLens-Signature": _sign(body),
                },
            )
            assert resp.status_code == 202, resp.text
            ack = resp.json()
            assert ack["receipts_created"] == 1
            assert ack["deduped"] is False

            # Verify a Receipt row landed for this user, tagged with
            # the message id.
            rows = (
                (await db_session.execute(select(Receipt).where(Receipt.user_id == user.id)))
                .scalars()
                .all()
            )
            assert len(list(rows)) == 1
            assert rows[0].external_message_id == payload["message_id"]
        finally:
            get_settings.cache_clear()

    async def test_dedupes_on_redelivery(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("INBOUND_EMAIL_SECRET", _TEST_SECRET)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            user = await create_user(
                db_session,
                email=f"dedup-{uuid4()}@example.com",
                password="hunter2hunter2",
            )

            payload = _payload(
                message_id="msg-redelivery-1",
                to=f"receipts+{user.inbox_token}@inbox.spendlens.local",
            )
            body = json.dumps(payload).encode()
            headers = {
                "Content-Type": "application/json",
                "X-SpendLens-Signature": _sign(body),
            }

            first = await client.post(f"{API}/inbound/email", content=body, headers=headers)
            assert first.status_code == 202
            assert first.json()["deduped"] is False

            # Re-deliver the same payload — provider retry. The
            # signature still verifies (same body, same timestamp);
            # the dedup happens at the message_id level.
            second = await client.post(f"{API}/inbound/email", content=body, headers=headers)
            assert second.status_code == 202
            assert second.json()["deduped"] is True
            assert second.json()["receipts_created"] == 1  # the existing one

            # Still exactly one receipt in the DB.
            rows = (
                (await db_session.execute(select(Receipt).where(Receipt.user_id == user.id)))
                .scalars()
                .all()
            )
            assert len(list(rows)) == 1
        finally:
            get_settings.cache_clear()

    async def test_skips_unsupported_attachment_silently(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("INBOUND_EMAIL_SECRET", _TEST_SECRET)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            user = await create_user(
                db_session,
                email=f"unsupported-{uuid4()}@example.com",
                password="hunter2hunter2",
            )

            # vCard attachment — common Outlook signature, never a
            # receipt. The webhook should accept the email but not
            # turn it into a receipt.
            payload = {
                "message_id": f"msg-{uuid4()}",
                "to": f"receipts+{user.inbox_token}@inbox.spendlens.local",
                "sender": "alice@example.com",
                "subject": "Sig",
                "body_plain": "",
                "attachments": [
                    {
                        "filename": "alice.vcf",
                        "content_type": "text/vcard",
                        "content_base64": base64.b64encode(b"BEGIN:VCARD").decode(),
                    }
                ],
            }
            body = json.dumps(payload).encode()
            resp = await client.post(
                f"{API}/inbound/email",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-SpendLens-Signature": _sign(body),
                },
            )
            assert resp.status_code == 202
            assert resp.json()["receipts_created"] == 0
        finally:
            get_settings.cache_clear()

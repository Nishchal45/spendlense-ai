"""Tests for the Gmail history-sync service (Phase 5.6 PR C).

Three layers:

* **Token refresh + REST adapters** — drive ``refresh_access_token``,
  ``list_added_message_ids``, ``get_message``, and
  ``get_attachment_bytes`` against an :class:`httpx.MockTransport`.
  No network.
* **Payload extraction** — the ``_extract_attachment_descriptors``
  walker plus ``_build_inbound_email`` against fixture message
  shapes (multipart trees, allowlist filtering).
* **End-to-end ``sync_connection``** — DB fixture + MockTransport
  combine to exercise: first push (cursor seeded, no work),
  subsequent push (refresh → history → message → attachment →
  receipt landed), cursor advanced.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.receipt import Receipt
from app.services.gmail_connection_service import upsert_connection
from app.services.gmail_oauth_service import (
    GmailNotConfiguredError,
    TokenExchangeError,
)
from app.services.gmail_sync_service import (
    GmailSyncError,
    _build_inbound_email,
    _extract_attachment_descriptors,
    _FetchedAttachment,
    get_attachment_bytes,
    get_message,
    list_added_message_ids,
    refresh_access_token,
    sync_connection,
)
from app.services.user_service import create_user

API = "/api/v1"


# 1×1 transparent PNG — the magic bytes that the receipt-service MIME
# sniff requires.
PNG_PIXEL = b"\x89PNG\r\n\x1a\n" + b"png-body" * 4


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> str:
    """Wire the OAuth client + Fernet key the sync layer depends on."""
    fernet_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_ID", "client.apps.googleusercontent.com")
    monkeypatch.setenv("GMAIL_OAUTH_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", fernet_key)
    get_settings.cache_clear()
    yield fernet_key
    get_settings.cache_clear()


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ----- 1. refresh_access_token -------------------------------------------


class TestRefreshAccessToken:
    @pytest.mark.asyncio
    async def test_returns_access_token(self, configured: str) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/token"
            return httpx.Response(
                200, json={"access_token": "a-1", "expires_in": 3600, "token_type": "Bearer"}
            )

        async with _client(handle) as c:
            assert await refresh_access_token(refresh_token="r", client=c) == "a-1"

    @pytest.mark.asyncio
    async def test_4xx_raises_token_exchange_error(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(401, json={"error": "invalid_grant"})) as c:
            with pytest.raises(TokenExchangeError, match="HTTP 401"):
                await refresh_access_token(refresh_token="r", client=c)

    @pytest.mark.asyncio
    async def test_response_missing_token_raises(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(200, json={"expires_in": 60})) as c:
            with pytest.raises(TokenExchangeError, match="missing"):
                await refresh_access_token(refresh_token="r", client=c)

    @pytest.mark.asyncio
    async def test_unconfigured_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GMAIL_OAUTH_CLIENT_SECRET", raising=False)
        get_settings.cache_clear()
        try:
            async with _client(lambda _r: httpx.Response(200, json={"access_token": "a"})) as c:
                with pytest.raises(GmailNotConfiguredError):
                    await refresh_access_token(refresh_token="r", client=c)
        finally:
            get_settings.cache_clear()


# ----- 2. list_added_message_ids -----------------------------------------


class TestListAddedMessageIds:
    @pytest.mark.asyncio
    async def test_collects_messages_added(self, configured: str) -> None:
        def handle(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "history": [
                        {
                            "id": "h1",
                            "messagesAdded": [
                                {"message": {"id": "m1"}},
                                {"message": {"id": "m2"}},
                            ],
                        }
                    ],
                    "historyId": "h1",
                },
            )

        async with _client(handle) as c:
            ids = await list_added_message_ids(access_token="a", start_history_id="100", client=c)
        assert ids == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_paginates_via_next_page_token(self, configured: str) -> None:
        # Two-page response. First page returns nextPageToken, second
        # page returns the final batch with no token.
        pages = iter(
            [
                httpx.Response(
                    200,
                    json={
                        "history": [{"id": "h1", "messagesAdded": [{"message": {"id": "m1"}}]}],
                        "nextPageToken": "p2",
                    },
                ),
                httpx.Response(
                    200,
                    json={
                        "history": [{"id": "h2", "messagesAdded": [{"message": {"id": "m2"}}]}],
                    },
                ),
            ]
        )

        def handle(_r: httpx.Request) -> httpx.Response:
            return next(pages)

        async with _client(handle) as c:
            ids = await list_added_message_ids(access_token="a", start_history_id="100", client=c)
        assert ids == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_404_returns_empty_list(self, configured: str) -> None:
        # Gmail returns 404 when startHistoryId is older than the
        # retention window. The caller treats it as "no messages",
        # not a hard failure — we'll resync from a fresher cursor.
        async with _client(lambda _r: httpx.Response(404, json={})) as c:
            ids = await list_added_message_ids(access_token="a", start_history_id="0", client=c)
        assert ids == []

    @pytest.mark.asyncio
    async def test_5xx_raises(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(503, text="boom")) as c:
            with pytest.raises(GmailSyncError, match="HTTP 503"):
                await list_added_message_ids(access_token="a", start_history_id="100", client=c)

    @pytest.mark.asyncio
    async def test_dedupes_duplicate_ids_across_pages(self, configured: str) -> None:
        # Same message id can appear in two history entries (for
        # instance after a labelAdded); our list should dedup.
        def handle(_r: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "history": [
                        {"id": "h1", "messagesAdded": [{"message": {"id": "dup"}}]},
                        {"id": "h2", "messagesAdded": [{"message": {"id": "dup"}}]},
                    ]
                },
            )

        async with _client(handle) as c:
            ids = await list_added_message_ids(access_token="a", start_history_id="100", client=c)
        assert ids == ["dup"]


# ----- 3. get_message + get_attachment_bytes -----------------------------


class TestGetMessageAndAttachment:
    @pytest.mark.asyncio
    async def test_get_message_returns_payload(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(200, json={"id": "m1", "snippet": "hi"})) as c:
            msg = await get_message(message_id="m1", access_token="a", client=c)
        assert msg["id"] == "m1"

    @pytest.mark.asyncio
    async def test_get_message_4xx_raises(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(404)) as c:
            with pytest.raises(GmailSyncError, match="HTTP 404"):
                await get_message(message_id="m1", access_token="a", client=c)

    @pytest.mark.asyncio
    async def test_get_attachment_decodes_base64url(self, configured: str) -> None:
        # Gmail uses URL-safe base64 *without* padding. Our decoder
        # adds padding so the standard library can take it.
        raw = b"\xff\xff\xfe"  # produces "__7-" sized output that needs padding
        encoded = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        async with _client(
            lambda _r: httpx.Response(200, json={"data": encoded, "size": len(raw)})
        ) as c:
            data = await get_attachment_bytes(
                message_id="m1", attachment_id="att1", access_token="a", client=c
            )
        assert data == raw

    @pytest.mark.asyncio
    async def test_get_attachment_missing_data_raises(self, configured: str) -> None:
        async with _client(lambda _r: httpx.Response(200, json={"size": 0})) as c:
            with pytest.raises(GmailSyncError, match="missing 'data'"):
                await get_attachment_bytes(
                    message_id="m1", attachment_id="a1", access_token="a", client=c
                )


# ----- 4. payload extraction ---------------------------------------------


def _gmail_message(
    *,
    message_id: str = "m1",
    attachments: list[tuple[str, str, str]] | None = None,
) -> dict[str, object]:
    """Synthesise a Gmail message tree with the given parts."""
    parts: list[dict[str, object]] = [
        {"mimeType": "text/plain", "body": {"data": "aGVsbG8="}},
    ]
    for filename, mime, att_id in attachments or []:
        parts.append(
            {
                "mimeType": mime,
                "filename": filename,
                "body": {"attachmentId": att_id, "size": 100},
            }
        )
    return {
        "id": message_id,
        "payload": {
            "headers": [
                {"name": "From", "value": "vendor@example.com"},
                {"name": "Subject", "value": "Your receipt"},
            ],
            "mimeType": "multipart/mixed",
            "parts": parts,
        },
    }


class TestPayloadExtraction:
    def test_extracts_only_allowed_mimes(self) -> None:
        message = _gmail_message(
            attachments=[
                ("receipt.png", "image/png", "att1"),
                ("signature.vcf", "text/vcard", "att2"),
                ("invoice.pdf", "application/pdf", "att3"),
            ]
        )
        descriptors = _extract_attachment_descriptors(message)
        ids = [d[2] for d in descriptors]
        assert "att1" in ids
        assert "att3" in ids
        assert "att2" not in ids  # vCard filtered out

    def test_walks_nested_parts(self) -> None:
        # multipart/mixed → multipart/related → image/png
        message = {
            "id": "m1",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [],
                "parts": [
                    {
                        "mimeType": "multipart/related",
                        "parts": [
                            {
                                "mimeType": "image/png",
                                "filename": "deep.png",
                                "body": {"attachmentId": "deep-att", "size": 1},
                            }
                        ],
                    }
                ],
            },
        }
        descriptors = _extract_attachment_descriptors(message)
        assert descriptors == [("deep.png", "image/png", "deep-att")]

    def test_skips_inline_parts_without_filename(self) -> None:
        # Inline image (filename empty, attachmentId present) — Gmail
        # uses this for embedded signatures. We don't treat them as
        # receipt attachments.
        message = {
            "id": "m1",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [],
                "parts": [
                    {
                        "mimeType": "image/png",
                        "filename": "",
                        "body": {"attachmentId": "inline", "size": 1},
                    }
                ],
            },
        }
        assert _extract_attachment_descriptors(message) == []

    def test_build_inbound_email_synthesises_to_address(self) -> None:
        message = _gmail_message(attachments=[("receipt.png", "image/png", "att1")])
        attachments = [
            _FetchedAttachment(filename="receipt.png", content_type="image/png", data=PNG_PIXEL)
        ]
        inbound = _build_inbound_email(
            message=message,
            attachments=attachments,
            inbox_token="0" * 32,
            inbox_email_domain="inbox.spendlens.local",
        )
        assert inbound.to == f"receipts+{'0' * 32}@inbox.spendlens.local"
        assert inbound.message_id == "m1"
        assert inbound.sender == "vendor@example.com"
        assert inbound.subject == "Your receipt"
        assert len(inbound.attachments) == 1
        # Body bytes survived the base64 round trip.
        assert base64.b64decode(inbound.attachments[0].content_base64) == PNG_PIXEL


# ----- 5. sync_connection — end-to-end DB-backed -------------------------


class TestSyncConnection:
    @pytest.mark.asyncio
    async def test_first_push_seeds_cursor_without_calling_google(
        self,
        db_session: AsyncSession,
        configured: str,
    ) -> None:
        user = await create_user(
            db_session, email=f"first-{uuid4()}@example.com", password="hunter2hunter2"
        )
        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="alice@gmail.com", refresh_token="r"
        )
        # Pre-condition: cursor is null (foundation default).
        assert connection.last_history_id is None

        # If the service called Google we'd see the AssertionError.
        def unreachable(_r: httpx.Request) -> httpx.Response:
            raise AssertionError("first push must not call Google")

        async with _client(unreachable) as c:
            result = await sync_connection(
                db_session,
                connection=connection,
                push_history_id="55",
                client=c,
            )
        assert result.messages_seen == 0
        assert result.receipts_created == 0
        assert connection.last_history_id == "55"

    @pytest.mark.asyncio
    async def test_subsequent_push_creates_receipt_via_inbound_pipeline(
        self,
        db_session: AsyncSession,
        configured: str,
    ) -> None:
        user = await create_user(
            db_session, email=f"sub-{uuid4()}@example.com", password="hunter2hunter2"
        )
        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="alice@gmail.com", refresh_token="r"
        )
        # Prime the cursor so we hit the real fetch path.
        connection.last_history_id = "100"
        await db_session.commit()

        # MockTransport that simulates: token refresh, history.list,
        # messages.get, attachments.get.
        attachment_data = base64.urlsafe_b64encode(PNG_PIXEL).decode().rstrip("=")

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/token":
                return httpx.Response(200, json={"access_token": "a-fresh", "expires_in": 3600})
            if path.endswith("/history"):
                return httpx.Response(
                    200,
                    json={
                        "history": [
                            {
                                "id": "h1",
                                "messagesAdded": [{"message": {"id": "msg-uber"}}],
                            }
                        ]
                    },
                )
            if path.endswith("/messages/msg-uber"):
                return httpx.Response(
                    200,
                    json=_gmail_message(
                        message_id="msg-uber",
                        attachments=[("receipt.png", "image/png", "att-1")],
                    ),
                )
            if path.endswith("/messages/msg-uber/attachments/att-1"):
                return httpx.Response(200, json={"data": attachment_data, "size": len(PNG_PIXEL)})
            raise AssertionError(f"unexpected request: {request.method} {path}")

        async with _client(handle) as c:
            result = await sync_connection(
                db_session,
                connection=connection,
                push_history_id="200",
                client=c,
            )
        assert result.messages_seen == 1
        assert result.receipts_created == 1
        assert connection.last_history_id == "200"

        # The inbound pipeline created a Receipt for this user with
        # the Gmail message id as the dedup key.
        rows = (
            (await db_session.execute(select(Receipt).where(Receipt.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].external_message_id == "msg-uber"

    @pytest.mark.asyncio
    async def test_message_with_no_attachments_is_a_noop(
        self,
        db_session: AsyncSession,
        configured: str,
    ) -> None:
        # Body-only confirmation email (Uber "your ride is on the way").
        # Sync runs cleanly, no receipts created.
        user = await create_user(
            db_session, email=f"noatt-{uuid4()}@example.com", password="hunter2hunter2"
        )
        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="alice@gmail.com", refresh_token="r"
        )
        connection.last_history_id = "100"
        await db_session.commit()

        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/token":
                return httpx.Response(200, json={"access_token": "a"})
            if path.endswith("/history"):
                return httpx.Response(
                    200,
                    json={
                        "history": [
                            {"id": "h", "messagesAdded": [{"message": {"id": "body-only"}}]}
                        ]
                    },
                )
            if path.endswith("/messages/body-only"):
                return httpx.Response(
                    200,
                    json={
                        "id": "body-only",
                        "payload": {
                            "mimeType": "text/plain",
                            "headers": [{"name": "From", "value": "x@y.com"}],
                            "body": {"data": base64.urlsafe_b64encode(b"hi").decode()},
                        },
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {path}")

        async with _client(handle) as c:
            result = await sync_connection(
                db_session,
                connection=connection,
                push_history_id="200",
                client=c,
            )
        assert result.receipts_created == 0
        assert connection.last_history_id == "200"

    @pytest.mark.asyncio
    async def test_history_too_old_completes_with_zero_receipts(
        self,
        db_session: AsyncSession,
        configured: str,
    ) -> None:
        user = await create_user(
            db_session, email=f"old-{uuid4()}@example.com", password="hunter2hunter2"
        )
        connection = await upsert_connection(
            db_session, user_id=user.id, google_email="alice@gmail.com", refresh_token="r"
        )
        connection.last_history_id = "0"
        await db_session.commit()

        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                return httpx.Response(200, json={"access_token": "a"})
            if request.url.path.endswith("/history"):
                return httpx.Response(404)
            raise AssertionError(f"unexpected request: {request.url}")

        async with _client(handle) as c:
            result = await sync_connection(
                db_session,
                connection=connection,
                push_history_id="999",
                client=c,
            )
        # No messages, no receipts; cursor advances to the push value
        # so the next push has a fresh anchor.
        assert result.receipts_created == 0
        assert connection.last_history_id == "999"

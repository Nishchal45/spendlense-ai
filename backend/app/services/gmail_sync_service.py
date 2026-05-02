"""Gmail incremental-sync logic shared by the Celery push handler.

The push delivery (verified in :mod:`gmail_pubsub_service`) tells us
*that* a user's mailbox changed; this module is what does the
fetching. Three responsibilities:

1. **Mint a fresh access token** from the stored, encrypted refresh
   token. Access tokens last an hour; we never persist them.
2. **Walk the Gmail history**: ``users.history.list`` returns every
   message added since our stored ``last_history_id``. For each new
   message id, ``users.messages.get`` returns the full message tree.
3. **Lift attachments into the canonical inbound-email shape** so
   :func:`process_inbound_email` from PR #34 stays the single
   receipt-creation path. Dedup, MIME validation, and Receipt
   creation all reuse that code.

We deliberately don't ship a Gmail-specific receipt parser. Email
forwarding (PR #34), share-target uploads, and Gmail push all funnel
through the same ``InboundEmail`` schema, which means new ingestion
sources (SMS, Slack, etc.) are a thin adapter on top of the same
service. ADR-0009 captures the trade-off.

Why ``httpx`` and not ``google-api-python-client``: the client lib
pulls 30 MB of generated stubs for an API surface we use six
endpoints of. A handful of typed POST/GET calls keeps the
dependency surface small and the test fakes obvious.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable
from dataclasses import dataclass

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.secret_box import decrypt_secret
from app.models.gmail_connection import GmailConnection
from app.schemas.inbound_email import InboundAttachment, InboundEmail
from app.services.gmail_oauth_service import (
    GOOGLE_TOKEN_URL,
    GmailNotConfiguredError,
    TokenExchangeError,
)
from app.services.inbound_email_service import (
    InboundEmailResult,
    process_inbound_email,
)

log = structlog.get_logger()


# Gmail REST endpoints. The literal ``me`` shorthand resolves to the
# authenticated user — saves us pulling the user id off the token.
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_HISTORY_URL = f"{_GMAIL_BASE}/history"
_MESSAGE_URL_TMPL = f"{_GMAIL_BASE}/messages/{{message_id}}"
_ATTACHMENT_URL_TMPL = f"{_GMAIL_BASE}/messages/{{message_id}}/attachments/{{attachment_id}}"

# Attachment MIMEs we forward into the receipt pipeline. Mirrors the
# direct-upload allowlist; the magic-byte sniff inside
# :mod:`receipt_service` is the actual gate, this just keeps us from
# round-tripping vCards and Outlook signature pixels.
_ALLOWED_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "application/pdf",
    }
)

# Only consider history records that *added* a message. ``labelAdded``
# / ``labelRemoved`` / ``messageDeleted`` events are irrelevant for a
# receipt pipeline — we want exactly the new-mail signal.
_HISTORY_TYPES = ("messagesAdded",)


class GmailSyncError(Exception):
    """Any non-recoverable failure during a sync attempt."""


@dataclass(frozen=True)
class _FetchedAttachment:
    """One Gmail attachment after we've downloaded the bytes."""

    filename: str
    content_type: str
    data: bytes


# ----- access token -------------------------------------------------------


async def refresh_access_token(*, refresh_token: str, client: httpx.AsyncClient) -> str:
    """Exchange a refresh token for a fresh access token.

    Raises :class:`GmailNotConfiguredError` when the OAuth client
    credentials are absent and :class:`TokenExchangeError` on any
    Google-side failure. Reusing the OAuth service's exception types
    keeps the worker's retry logic uniform across error sources.
    """
    settings = get_settings()
    if not (settings.gmail_oauth_client_id and settings.gmail_oauth_client_secret):
        raise GmailNotConfiguredError("Gmail OAuth client credentials are not set")

    resp = await client.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": settings.gmail_oauth_client_id,
            "client_secret": settings.gmail_oauth_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        headers={"Accept": "application/json"},
    )
    if resp.status_code != httpx.codes.OK:
        log.warning(
            "gmail_sync.refresh_failed",
            status=resp.status_code,
            body=resp.text[:500],
        )
        raise TokenExchangeError(f"refresh failed: HTTP {resp.status_code}")

    body = resp.json()
    access_token = body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise TokenExchangeError("refresh response missing access_token")
    return access_token


# ----- Gmail REST calls ---------------------------------------------------


async def list_added_message_ids(
    *,
    access_token: str,
    start_history_id: str,
    client: httpx.AsyncClient,
) -> list[str]:
    """Walk ``users.history.list`` and collect every newly-added message id.

    Pagination via ``nextPageToken``. Bounded by Gmail's own response
    size — we don't slice further. Duplicate message ids across
    pages are de-duplicated to a stable, insertion-ordered list.
    """
    seen: dict[str, None] = {}
    page_token: str | None = None

    while True:
        params: dict[str, str] = {
            "startHistoryId": start_history_id,
            "historyTypes": "messageAdded",
        }
        if page_token:
            params["pageToken"] = page_token

        resp = await client.get(
            _HISTORY_URL,
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code == httpx.codes.NOT_FOUND:
            # Gmail returns 404 when ``startHistoryId`` is older
            # than the user's history retention window (Gmail keeps
            # roughly a week of history). Caller should resync from
            # the latest historyId — we surface the empty-result
            # signal here.
            log.warning("gmail_sync.history_too_old", start_history_id=start_history_id)
            return []
        if resp.status_code != httpx.codes.OK:
            raise GmailSyncError(f"history.list failed: HTTP {resp.status_code} {resp.text[:200]}")

        body = resp.json()
        for entry in body.get("history") or []:
            for record_type in _HISTORY_TYPES:
                for added in entry.get(record_type) or []:
                    msg = added.get("message")
                    if isinstance(msg, dict):
                        mid = msg.get("id")
                        if isinstance(mid, str) and mid:
                            seen.setdefault(mid, None)

        next_token = body.get("nextPageToken")
        if not isinstance(next_token, str) or not next_token:
            break
        page_token = next_token

    return list(seen.keys())


async def get_message(
    *, message_id: str, access_token: str, client: httpx.AsyncClient
) -> dict[str, object]:
    """Fetch one Gmail message in ``full`` format (default)."""
    resp = await client.get(
        _MESSAGE_URL_TMPL.format(message_id=message_id),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != httpx.codes.OK:
        raise GmailSyncError(f"messages.get failed for {message_id}: HTTP {resp.status_code}")
    return dict(resp.json())


async def get_attachment_bytes(
    *,
    message_id: str,
    attachment_id: str,
    access_token: str,
    client: httpx.AsyncClient,
) -> bytes:
    """Fetch one attachment's body and decode the base64url payload."""
    resp = await client.get(
        _ATTACHMENT_URL_TMPL.format(message_id=message_id, attachment_id=attachment_id),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code != httpx.codes.OK:
        raise GmailSyncError(
            f"attachments.get failed for {message_id}/{attachment_id}: " f"HTTP {resp.status_code}"
        )
    body = resp.json()
    data_b64url = body.get("data")
    if not isinstance(data_b64url, str) or not data_b64url:
        raise GmailSyncError(f"attachments.get for {message_id}/{attachment_id} missing 'data'")
    # Gmail uses URL-safe base64 *without* padding. ``urlsafe_b64decode``
    # requires correct padding; pad up to a multiple of 4 manually.
    padded = data_b64url + "=" * ((4 - len(data_b64url) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as exc:
        raise GmailSyncError(
            f"attachments.get for {message_id}/{attachment_id} " "returned invalid base64url"
        ) from exc


# ----- payload walking ----------------------------------------------------


def _walk_parts(payload: object) -> Iterable[dict[str, object]]:
    """Yield every leaf ``part`` dict in a Gmail message payload tree.

    Gmail nests parts arbitrarily deep (multipart/alternative inside
    multipart/mixed inside multipart/related, etc.). A receipt JPEG
    can sit at any depth. Recursion handles the general case without
    assumptions about structure.
    """
    if not isinstance(payload, dict):
        return
    parts = payload.get("parts")
    if isinstance(parts, list):
        for part in parts:
            yield from _walk_parts(part)
    else:
        yield payload


def _extract_attachment_descriptors(
    message: dict[str, object],
) -> list[tuple[str, str, str]]:
    """Find ``(filename, mime, attachment_id)`` tuples worth fetching.

    Filters on:
    * ``attachmentId`` present (so the bytes live in
      ``attachments.get``, not inline).
    * ``mimeType`` in the allowlist.
    * Non-empty filename — Gmail uses ``""`` for inline parts, which
      we don't treat as attachments.
    """
    out: list[tuple[str, str, str]] = []
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return out

    for part in _walk_parts(payload):
        mime = part.get("mimeType")
        filename = part.get("filename")
        body = part.get("body")
        if not isinstance(mime, str) or mime not in _ALLOWED_MIMES:
            continue
        if not isinstance(filename, str) or not filename:
            continue
        if not isinstance(body, dict):
            continue
        attachment_id = body.get("attachmentId")
        if isinstance(attachment_id, str) and attachment_id:
            out.append((filename, mime, attachment_id))
    return out


def _header(message: dict[str, object], name: str) -> str:
    """Read one RFC 5322 header from the message payload, case-insensitive."""
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return ""
    target = name.lower()
    for header in headers:
        if not isinstance(header, dict):
            continue
        if str(header.get("name", "")).lower() == target:
            return str(header.get("value", ""))
    return ""


def _build_inbound_email(
    *,
    message: dict[str, object],
    attachments: list[_FetchedAttachment],
    inbox_token: str,
    inbox_email_domain: str,
) -> InboundEmail:
    """Project a Gmail message into the canonical :class:`InboundEmail`.

    The ``to`` field is synthesised — Gmail's actual ``To`` header
    is the user's Gmail address, not our forward-to address. We
    swap in ``receipts+<inbox_token>@<domain>`` so the existing
    inbound-email service's user-resolution path works unchanged.
    """
    message_id = message.get("id")
    if not isinstance(message_id, str) or not message_id:
        raise GmailSyncError("Gmail message missing 'id'")

    sender = _header(message, "From") or "unknown@gmail.com"
    subject = _header(message, "Subject")

    return InboundEmail(
        message_id=message_id,
        to=f"receipts+{inbox_token}@{inbox_email_domain}",
        sender=sender,
        subject=subject,
        body_plain="",
        attachments=[
            InboundAttachment(
                filename=a.filename,
                content_type=a.content_type,
                content_base64=base64.b64encode(a.data).decode("ascii"),
            )
            for a in attachments
        ],
    )


# ----- orchestration ------------------------------------------------------


@dataclass(frozen=True)
class SyncResult:
    """Aggregate outcome of one sync run."""

    messages_seen: int
    receipts_created: int
    new_history_id: str | None


async def sync_connection(
    session: AsyncSession,
    *,
    connection: GmailConnection,
    push_history_id: str,
    client: httpx.AsyncClient,
) -> SyncResult:
    """Drive an end-to-end sync for one Gmail connection.

    First push for a connection (``last_history_id`` is NULL): we
    can't compute a delta, so we seed the cursor from the push's
    own ``historyId`` and skip processing this trigger event. The
    next push, with the cursor primed, processes normally. Gmail's
    docs explicitly recommend this pattern — the first push announces
    a state we have no baseline for.

    Subsequent pushes: delta from ``connection.last_history_id`` to
    now. We update the cursor *after* successful processing so a
    crash mid-run replays the same delta on retry.
    """
    if connection.last_history_id is None:
        # Seed and persist; no work to do this round.
        connection.last_history_id = push_history_id
        await session.commit()
        log.info(
            "gmail_sync.cursor_seeded",
            connection_id=str(connection.id),
            history_id=push_history_id,
        )
        return SyncResult(messages_seen=0, receipts_created=0, new_history_id=push_history_id)

    settings = get_settings()
    refresh_token = decrypt_secret(connection.encrypted_refresh_token)
    access_token = await refresh_access_token(refresh_token=refresh_token, client=client)

    message_ids = await list_added_message_ids(
        access_token=access_token,
        start_history_id=connection.last_history_id,
        client=client,
    )

    receipts_created = 0
    user = connection.user
    for message_id in message_ids:
        message = await get_message(message_id=message_id, access_token=access_token, client=client)
        descriptors = _extract_attachment_descriptors(message)
        if not descriptors:
            # Body-only / unsupported-attachment messages are a
            # no-op rather than a failure. Phase 6+ may add LLM-
            # over-body parsing for receipt confirmations like
            # Uber's "Your Tuesday morning ride" emails.
            continue

        fetched: list[_FetchedAttachment] = []
        for filename, mime, attachment_id in descriptors:
            data = await get_attachment_bytes(
                message_id=message_id,
                attachment_id=attachment_id,
                access_token=access_token,
                client=client,
            )
            fetched.append(_FetchedAttachment(filename=filename, content_type=mime, data=data))

        payload = _build_inbound_email(
            message=message,
            attachments=fetched,
            inbox_token=user.inbox_token,
            inbox_email_domain=settings.inbox_email_domain,
        )
        result: InboundEmailResult = await process_inbound_email(session, payload=payload)
        if not result.deduped:
            receipts_created += len(result.receipts_created)

    # Cursor advances to the *push's* historyId, not the
    # last-seen-message id, so a subsequent push that arrives before
    # any new mail still computes a coherent delta.
    connection.last_history_id = push_history_id
    await session.commit()

    log.info(
        "gmail_sync.completed",
        connection_id=str(connection.id),
        messages_seen=len(message_ids),
        receipts_created=receipts_created,
        new_history_id=push_history_id,
    )
    return SyncResult(
        messages_seen=len(message_ids),
        receipts_created=receipts_created,
        new_history_id=push_history_id,
    )

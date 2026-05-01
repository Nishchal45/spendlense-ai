"""Inbound-email webhook processing.

Two things this module does:

1. **Verify the signature** the provider attaches to every webhook
   delivery. The scheme follows Stripe's pattern (a single
   ``X-SpendLens-Signature: t=<unix>,v1=<hex hmac>`` header,
   HMAC-SHA256 of ``"<timestamp>.<raw body>"`` with the shared
   ``inbound_email_secret``). Verifies the timestamp is within a 5-
   minute window so a captured request can't be replayed indefinitely.
2. **Process the parsed payload**: resolve the ``to`` address back
   to a user via the ``inbox_token``, dedup on ``message_id``, and
   create one Receipt per allowed-MIME attachment using the same
   service the direct-upload path uses. Each receipt is enqueued
   for OCR exactly like a normal upload.

What this module deliberately doesn't do:

* **Parse provider-specific payloads.** Postmark / SES / Mailgun
  each send different JSON; an adapter layer (out of scope here)
  translates them into :class:`InboundEmail` first. The webhook
  endpoint receives the canonical shape.
* **Decide what's an attachment vs. an inline asset.** The provider
  has already split them; we just iterate ``attachments``. Body-
  only receipts (some Uber / DoorDash emails) are a Phase 6+ item.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import re
from dataclasses import dataclass
from hashlib import sha256
from time import time
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.receipt import Receipt
from app.models.user import User
from app.schemas.inbound_email import InboundEmail
from app.services.receipt_service import (
    UnsupportedMediaTypeError,
    create_receipt,
)

log = structlog.get_logger()

# ----- signature verification --------------------------------------------

# Stripe-style signature header: ``t=<unix>,v1=<hex>``. Versioning
# the algorithm with ``v1`` means we can rotate to a newer scheme
# (e.g. Ed25519) without breaking the parser.
_SIG_HEADER_RE = re.compile(r"^t=(?P<ts>\d+),v1=(?P<sig>[0-9a-f]+)$")

# Reject deliveries older than 5 minutes. A captured request is
# meaningfully replay-protected within that window only if the
# ``message_id`` dedup is also working — the timestamp by itself
# only buys "narrow re-delivery window".
_REPLAY_WINDOW_SECONDS = 5 * 60


class InvalidSignatureError(Exception):
    """Webhook signature didn't verify or timestamp is stale."""


class UnknownInboxTokenError(Exception):
    """The ``to`` address didn't resolve to a known user."""


def verify_webhook_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
    now: float | None = None,
) -> None:
    """Validate the signature header against ``raw_body``.

    Raises :class:`InvalidSignatureError` on any failure path:
    missing header, malformed header, stale timestamp, mismatched
    HMAC. Returns ``None`` on success.

    ``now`` is injectable for tests; production callers omit it and
    let the function read the real clock.
    """
    if not signature_header:
        raise InvalidSignatureError("missing signature header")

    match = _SIG_HEADER_RE.match(signature_header.strip())
    if not match:
        raise InvalidSignatureError("malformed signature header")

    timestamp = int(match.group("ts"))
    signature = match.group("sig")

    actual_now = now if now is not None else time()
    if abs(actual_now - timestamp) > _REPLAY_WINDOW_SECONDS:
        raise InvalidSignatureError("timestamp outside replay window")

    # ``"<ts>.<raw body>"`` is the canonical signing string. Including
    # the timestamp inside the HMAC is what stops an attacker from
    # swapping ``t=`` to dodge the replay check.
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + raw_body,
        sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        # ``compare_digest`` runs in constant time — a casual ==
        # would leak the matching prefix length to a determined
        # attacker.
        raise InvalidSignatureError("signature mismatch")


# ----- token resolution ---------------------------------------------------

# ``receipts+<32-hex>@anything`` — the address the user pasted into a
# Gmail filter. We extract the token regardless of the configured
# ``inbox_email_domain`` so a misconfigured DNS doesn't take down the
# webhook (the bigger risk is "we accept mail to the wrong domain"
# but that's bounded by the provider's MX setup, not this regex).
_TOKEN_FROM_TO_RE = re.compile(r"receipts\+([0-9a-f]{32})@", re.IGNORECASE)


async def resolve_user_by_to_address(
    session: AsyncSession,
    *,
    to_address: str,
) -> User:
    """Map ``receipts+<token>@...`` back to a user. 404-shaped error
    if the token doesn't resolve."""
    match = _TOKEN_FROM_TO_RE.search(to_address.lower())
    if not match:
        raise UnknownInboxTokenError(to_address)

    token = match.group(1)
    user = (
        await session.execute(select(User).where(User.inbox_token == token))
    ).scalar_one_or_none()
    if user is None:
        raise UnknownInboxTokenError(token)
    return user


# ----- processing ---------------------------------------------------------

# Allowed attachment MIMEs mirror direct uploads. The backend's
# magic-byte sniff is the actual gate; this filter keeps PDFs and
# image attachments without paying the round-trip cost on a
# 4 MB Outlook-signature ``image/png`` we don't care about.
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


@dataclass(frozen=True)
class InboundEmailResult:
    """Outcome of one inbound delivery."""

    message_id: str
    receipts_created: list[UUID]
    deduped: bool


async def process_inbound_email(
    session: AsyncSession,
    *,
    payload: InboundEmail,
) -> InboundEmailResult:
    """Resolve user, dedup, and create receipts from the attachments.

    Idempotent on ``message_id``: re-delivery of the same email
    returns ``deduped=True`` with no new receipts. The check is
    per-user so two users could conceivably end up with the same
    provider-message-id (very unlikely in practice) without
    blocking each other.
    """
    user = await resolve_user_by_to_address(session, to_address=str(payload.to))

    # Dedup: any existing receipt with this ``external_message_id``
    # for *this user* short-circuits.
    existing = (
        (
            await session.execute(
                select(Receipt.id).where(
                    Receipt.user_id == user.id,
                    Receipt.external_message_id == payload.message_id,
                )
            )
        )
        .scalars()
        .all()
    )
    if existing:
        log.info(
            "inbound_email.duplicate",
            user_id=str(user.id),
            message_id=payload.message_id,
        )
        return InboundEmailResult(
            message_id=payload.message_id,
            receipts_created=list(existing),
            deduped=True,
        )

    created: list[UUID] = []
    for attachment in payload.attachments:
        if attachment.content_type not in _ALLOWED_MIMES:
            # Skipped silently — Outlook signature pixels and
            # vCards are noise, not errors.
            continue
        try:
            body = base64.b64decode(attachment.content_base64, validate=True)
        except binascii.Error:
            log.warning(
                "inbound_email.bad_base64",
                user_id=str(user.id),
                message_id=payload.message_id,
                filename=attachment.filename,
            )
            continue

        try:
            receipt = await create_receipt(
                session,
                user_id=user.id,
                body=body,
                external_message_id=payload.message_id,
            )
        except UnsupportedMediaTypeError:
            # Magic-byte sniff disagreed with the declared MIME.
            # Skip rather than fail the whole webhook — the user's
            # other attachments may still be good.
            log.warning(
                "inbound_email.skipped_unsupported",
                user_id=str(user.id),
                message_id=payload.message_id,
                filename=attachment.filename,
            )
            continue
        created.append(receipt.id)

    log.info(
        "inbound_email.processed",
        user_id=str(user.id),
        message_id=payload.message_id,
        receipts_created=len(created),
    )
    return InboundEmailResult(
        message_id=payload.message_id,
        receipts_created=created,
        deduped=False,
    )

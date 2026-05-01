"""Wire contracts for the inbound-email webhook.

Provider-agnostic shape — the dashboard configures one of Postmark /
SES Inbound / Mailgun Routes to POST a normalised payload here.
Provider-specific adapters (parsing each vendor's JSON into this
shape) are out of scope for the ADR-0008 PR; the canonical example
is documented in the ADR follow-ups.

Attachments arrive **inline as base64**, not as URLs. A URL would
mean a second network hop on the worker — fine in production, but a
nightmare in tests because the URL would have to resolve. Inline
keeps the contract self-describing and lets a curl-driven local
test work end-to-end.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Stop a hostile / buggy provider from posting absurd payloads. The
# 10 MB cap on direct uploads is enforced again here in case an
# email arrives with one giant attachment; the per-attachment loop
# in the service layer also re-checks each part.
MAX_INBOUND_ATTACHMENTS = 10
MAX_INBOUND_PAYLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB total payload incl. headers


class InboundAttachment(BaseModel):
    """One file attached to the inbound email."""

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)
    # Base64 of the file body. We deliberately don't bound the length
    # at the schema layer — the per-part size cap enforced in the
    # service layer keeps the bound consistent with direct uploads
    # (10 MiB), and base64 inflates by ~33%.
    content_base64: str


class InboundEmail(BaseModel):
    """Provider-agnostic inbound-email payload.

    All fields are mandatory except the body so a malformed delivery
    fails at validation rather than mid-processing. ``message_id`` is
    used for dedup so retries / forward-rule loops don't double-book.
    """

    message_id: str = Field(min_length=1, max_length=255)
    # ``str`` rather than ``EmailStr`` because Pydantic's email
    # validator (via ``email-validator``) rejects reserved TLDs like
    # ``.local`` and ``.test`` used in self-hosted / dev setups. The
    # *real* recipient validation is the ``receipts+<token>@`` regex
    # in :mod:`app.services.inbound_email_service`; if that doesn't
    # match, the lookup fails and the route returns 404.
    to: str = Field(min_length=3, max_length=320)
    sender: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=998)  # RFC 5322 line cap
    body_plain: str = Field(default="", max_length=1_048_576)  # 1 MiB
    attachments: list[InboundAttachment] = Field(
        default_factory=list, max_length=MAX_INBOUND_ATTACHMENTS
    )


class InboundEmailAck(BaseModel):
    """Response shape — what we tell the provider after a successful
    delivery. The fields let a debug-minded operator see whether the
    email actually produced any receipts (some emails don't have
    attachments, which is a no-op rather than an error)."""

    message_id: str
    receipts_created: int
    deduped: bool

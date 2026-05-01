"""Inbound-email webhook endpoint.

The route deliberately does **not** require auth — the request comes
from the configured email provider, not the user. Authentication is
the per-request HMAC signature; everything else routes through the
service-layer logic.

Status codes:

* ``202`` — accepted (with or without dedup; the response body
  carries the count of new receipts so an operator can tell).
* ``400`` — malformed payload (JSON didn't validate).
* ``401`` — missing or invalid signature, or stale timestamp.
* ``404`` — the ``to`` address didn't resolve to a known user. We
  return 404 (not 422) on the theory that the payload was
  syntactically fine but semantically pointed at a non-existent
  inbox — a typo'd Gmail rule, not a malformed POST.
* ``503`` — webhook is misconfigured (no secret in env). Returning
  503 instead of silently 401-ing makes the misconfiguration
  visible to ops.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from app.api.v1.deps import SessionDep
from app.core.config import get_settings
from app.schemas.inbound_email import InboundEmail, InboundEmailAck
from app.services.inbound_email_service import (
    InvalidSignatureError,
    UnknownInboxTokenError,
    process_inbound_email,
    verify_webhook_signature,
)

router = APIRouter(prefix="/inbound", tags=["inbound"])
log = structlog.get_logger()


@router.post(
    "/email",
    response_model=InboundEmailAck,
    status_code=status.HTTP_202_ACCEPTED,
)
async def inbound_email(
    request: Request,
    session: SessionDep,
    x_spendlens_signature: str | None = Header(default=None, alias="X-SpendLens-Signature"),
) -> InboundEmailAck:
    """Accept a single inbound-email delivery from the provider."""
    settings = get_settings()
    if not settings.inbound_email_secret:
        # The webhook is wired up but the secret isn't set — log
        # loudly and return 503 so the provider's retry loop sees
        # the failure rather than an assumed 401.
        log.error("inbound_email.no_secret_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inbound email is not configured on this server",
        )

    raw_body = await request.body()

    try:
        verify_webhook_signature(
            raw_body=raw_body,
            signature_header=x_spendlens_signature,
            secret=settings.inbound_email_secret,
        )
    except InvalidSignatureError as exc:
        # Logged at warn — repeated bad signatures from the same
        # source are a real signal worth alerting on.
        log.warning("inbound_email.bad_signature", reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook signature",
        ) from exc

    # Pydantic validation is the second gate — the signature pass
    # tells us the bytes came from our provider, not that they're
    # well-shaped.
    try:
        payload = InboundEmail.model_validate_json(raw_body)
    except ValidationError as exc:
        log.warning("inbound_email.bad_payload", errors=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inbound email payload failed validation",
        ) from exc

    try:
        result = await process_inbound_email(session, payload=payload)
    except UnknownInboxTokenError as exc:
        log.warning("inbound_email.unknown_token", to=str(payload.to))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No SpendLens user matched the recipient address",
        ) from exc

    log.info(
        "inbound_email.accepted",
        message_id=result.message_id,
        receipts_created=len(result.receipts_created),
        deduped=result.deduped,
    )
    return InboundEmailAck(
        message_id=result.message_id,
        receipts_created=len(result.receipts_created),
        deduped=result.deduped,
    )

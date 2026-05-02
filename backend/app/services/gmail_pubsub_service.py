"""Verify Google Cloud Pub/Sub push deliveries and decode the payload.

Google signs every push delivery with an OIDC ID-token JWT in the
``Authorization: Bearer`` header. The JWT's claims tell us:

* ``iss`` = ``https://accounts.google.com`` — Google's OIDC issuer.
  Pinned, not configurable, because anything else is by definition
  not a Google delivery.
* ``aud`` = whatever audience we configured on the push subscription
  (typically the push endpoint URL). Comes from
  ``settings.gmail_pubsub_audience``.
* ``email`` = the service account configured on the subscription.
  Comes from ``settings.gmail_pubsub_service_account``.

The push body itself is shaped::

    {
      "message": {
        "data": <base64 of {"emailAddress": "...", "historyId": ...}>,
        "messageId": "...",
        "publishTime": "..."
      },
      "subscription": "projects/<project>/subscriptions/<sub>"
    }

We verify in this order: JWT signature → ``aud`` claim → ``iss``
claim → service-account ``email`` → decode the message body.
Mismatches at any step raise :class:`PushVerificationError` so the
endpoint can return a single 401.

Why this lives in its own service: the JWT verification depends on
``google-auth`` and the verification call shells out to Google's
public-cert endpoint. Keeping it isolated means tests can swap the
verifier with a stub via monkeypatch and never touch the network.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import get_settings

log = structlog.get_logger()


# Google's OIDC issuer for ID tokens. Pinned because the only
# legitimate value is this exact string — making it configurable
# would only ever weaken the check.
_GOOGLE_OIDC_ISSUER = "https://accounts.google.com"


class PushVerificationError(Exception):
    """JWT signature/claims didn't verify, or the body is malformed."""


class PushNotConfiguredError(Exception):
    """``gmail_pubsub_audience`` / service-account env vars are unset."""


# Type of a ``verify_oauth2_token``-shaped verifier. The middle arg is
# ``google-auth``'s transport ``Request`` object, which we keep opaque
# here (typed as ``Any``) so this module doesn't import the transport
# layer at top level — that import requires ``urllib3`` and triggers
# a fresh ``ImportError`` on Python distros without it. Tests pass a
# stub callable; production builds the real one inside
# :func:`_default_verifier`.
JWTVerifier = Callable[[str, Any, str], dict[str, Any]]


def _default_verifier() -> JWTVerifier:
    """The real Google-cert-backed verifier. Tests pass a stub instead.

    Imports are lazy so module load doesn't depend on ``google-auth``'s
    transport requirements. The verifier closure binds a single
    long-lived ``urllib3`` transport object — Google's certs rotate
    every few hours, but ``google-auth`` caches the JWKS internally,
    so a per-call client wouldn't help and would just churn TLS
    handshakes.
    """
    from google.auth.transport import urllib3 as google_urllib3
    from google.oauth2 import id_token
    from urllib3 import PoolManager

    # ``google-auth``'s public surface lacks ``py.typed`` for these
    # callables, so mypy sees them as untyped. Suppressing only the
    # call site keeps the rest of the module under strict typing.
    transport = google_urllib3.Request(PoolManager())  # type: ignore[no-untyped-call]

    def verify(token: str, _request: Any, audience: str) -> dict[str, Any]:
        result: dict[str, Any] = id_token.verify_oauth2_token(  # type: ignore[no-untyped-call]
            token, transport, audience
        )
        return result

    return verify


@dataclass(frozen=True)
class PubSubMessage:
    """The two fields we care about from a Gmail Pub/Sub push.

    ``email_address`` is the user's Gmail address (looks up to a
    ``gmail_connections`` row). ``history_id`` is Gmail's monotonic
    cursor — when this push arrives, the user's mailbox has changes
    we can fetch via ``users.history.list?startHistoryId=<our last>``.

    ``raw_history_id`` is the trigger value, kept distinct from the
    stored cursor: we use it to seed an empty cursor on first push
    but always pull deltas relative to ``connection.last_history_id``.
    """

    email_address: str
    history_id: str
    pubsub_message_id: str


def verify_push_request(
    *,
    authorization_header: str | None,
    raw_body: bytes,
    verifier: JWTVerifier | None = None,
) -> PubSubMessage:
    """Verify the JWT and return the decoded Gmail Pub/Sub payload.

    Raises:
        :class:`PushNotConfiguredError` when the env vars governing
        verification (audience + service account) are unset.
        :class:`PushVerificationError` on any failure path: missing /
        malformed JWT header, bad signature, wrong audience, wrong
        issuer, wrong service-account email, malformed Pub/Sub body,
        missing required fields.
    """
    settings = get_settings()
    audience = settings.gmail_pubsub_audience
    service_account = settings.gmail_pubsub_service_account
    if not (audience and service_account):
        raise PushNotConfiguredError("GMAIL_PUBSUB_AUDIENCE / GMAIL_PUBSUB_SERVICE_ACCOUNT not set")

    if not authorization_header:
        raise PushVerificationError("missing Authorization header")
    bearer_prefix = "Bearer "
    if not authorization_header.startswith(bearer_prefix):
        raise PushVerificationError("malformed Authorization header")
    jwt_token = authorization_header[len(bearer_prefix) :].strip()
    if not jwt_token:
        raise PushVerificationError("empty bearer token")

    verify = verifier or _default_verifier()
    try:
        # ``verify_oauth2_token`` checks the signature against
        # Google's published certs, validates ``exp`` / ``iat``,
        # and enforces ``aud``. We do the ``iss`` and ``email``
        # checks ourselves below — the library doesn't pin them
        # at the call signature level. The middle arg is the bound
        # transport on the verifier closure; passing ``None`` here
        # is fine because the closure ignores it.
        claims = verify(jwt_token, None, audience)
    except ValueError as exc:
        # ``google-auth`` raises a plain ``ValueError`` for every
        # JWT failure (bad signature, wrong audience, expired). We
        # wrap into our typed barrier so the endpoint has one
        # ``except`` arm.
        raise PushVerificationError(f"JWT verification failed: {exc}") from exc

    issuer = claims.get("iss")
    if issuer != _GOOGLE_OIDC_ISSUER:
        raise PushVerificationError(f"unexpected issuer: {issuer!r}")

    email = claims.get("email")
    if email != service_account:
        raise PushVerificationError("JWT email claim does not match configured service account")

    return _decode_pubsub_body(raw_body)


def _decode_pubsub_body(raw_body: bytes) -> PubSubMessage:
    """Parse the outer Pub/Sub envelope and the inner Gmail JSON.

    Two stages: outer JSON gives us ``message.data`` (base64 of the
    Gmail notification) and ``message.messageId``. Inner JSON has
    ``emailAddress`` and ``historyId``. Each stage validates types
    so a malformed delivery never reaches the worker.
    """
    try:
        envelope = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise PushVerificationError("Pub/Sub body is not valid JSON") from exc

    if not isinstance(envelope, dict):
        raise PushVerificationError("Pub/Sub body is not a JSON object")

    message = envelope.get("message")
    if not isinstance(message, dict):
        raise PushVerificationError("Pub/Sub body missing 'message' object")

    data_b64 = message.get("data")
    if not isinstance(data_b64, str) or not data_b64:
        raise PushVerificationError("Pub/Sub message missing 'data'")

    pubsub_message_id = message.get("messageId")
    if not isinstance(pubsub_message_id, str) or not pubsub_message_id:
        raise PushVerificationError("Pub/Sub message missing 'messageId'")

    try:
        # Pub/Sub uses standard base64 (with padding), not the
        # base64url variant Gmail attachments use. Validate strictly
        # so a corrupted envelope fails fast.
        data_bytes = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PushVerificationError("Pub/Sub data is not valid base64") from exc

    try:
        inner = json.loads(data_bytes)
    except json.JSONDecodeError as exc:
        raise PushVerificationError("Pub/Sub data is not valid JSON") from exc

    if not isinstance(inner, dict):
        raise PushVerificationError("Pub/Sub data is not a JSON object")

    email_address = inner.get("emailAddress")
    if not isinstance(email_address, str) or not email_address:
        raise PushVerificationError("Gmail notification missing 'emailAddress'")

    history_id_raw = inner.get("historyId")
    # Gmail sends ``historyId`` as a *number* in the JSON; we coerce
    # to string because the column it lands in is ``String(64)`` —
    # 64-bit integers serialised as text dodge any Postgres
    # numeric-overflow corner case the upstream API might add.
    if isinstance(history_id_raw, int):
        history_id = str(history_id_raw)
    elif isinstance(history_id_raw, str) and history_id_raw:
        history_id = history_id_raw
    else:
        raise PushVerificationError("Gmail notification missing 'historyId'")

    return PubSubMessage(
        email_address=email_address,
        history_id=history_id,
        pubsub_message_id=pubsub_message_id,
    )

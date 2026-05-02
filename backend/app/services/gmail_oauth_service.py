"""Google OAuth 2.0 flow for Gmail read access.

Phase 5.6 surface: build a consent URL, accept the redirect-back,
exchange the code for tokens, and best-effort revoke on disconnect.
The Pub/Sub push handler that uses the resulting refresh token lives
in PR C.

Flow choice: **Web Server** (authorization-code, with secret) — we
have a backend, and the refresh token must be persisted server-side
for the Pub/Sub push worker to refresh access tokens behind the
user's back. PKCE would be the right call for a public client (SPA-
only, no backend); we deliberately don't use it.

Trust model:

* The ``state`` parameter is a HMAC-signed envelope of
  ``(user_id, nonce, issued_at)``. Google round-trips it verbatim.
  At the callback we re-verify the HMAC, check the timestamp is
  within :data:`_STATE_TTL_SECONDS`, and treat the encoded
  ``user_id`` as the authenticated subject. That way the callback
  doesn't need a session cookie or bearer token — the state IS the
  authenticator. CSRF is closed because an attacker can't mint a
  valid state without ``JWT_SECRET``.
* All HTTP to Google goes through an injected
  :class:`httpx.AsyncClient`. Tests pass a client built on top of
  :class:`httpx.MockTransport`; production passes a fresh client per
  request. We deliberately don't share a module-global client — the
  surface is small enough that the connection pool gain wouldn't
  show up in a flame graph.

Failure model: every Google call that fails for a reason the user
should see (4xx from the token endpoint, malformed userinfo) raises
a typed exception the route maps to a 4xx. Network errors / 5xx
bubble as :class:`TokenExchangeError` and become 502s — they're
genuinely "Google's fault" and retrying later usually fixes them.
"""

from __future__ import annotations

import hmac
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from urllib.parse import urlencode
from uuid import UUID

import httpx
import structlog

from app.core.config import get_settings

log = structlog.get_logger()


# ----- constants ---------------------------------------------------------

# Google's OAuth + userinfo endpoints. Centralised here so a future
# Google API breaking-change patch is one diff. Don't move these to
# ``Settings`` — they're not deployment-tunable, and an env var that
# could swing them at runtime is an exfil vector if a server gets
# misconfigured.
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

# ``gmail.readonly`` is the smallest scope that lets us read message
# bodies + attachments. Gmail's ``metadata`` scope skips the body
# (cheaper for inbox-zero apps but useless for receipts). We never
# ask for ``gmail.modify`` — we don't need to mark messages read or
# move them, and the scope doubles the consent screen's friction.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# State envelope is valid for 10 minutes. The user has to complete
# the consent screen + redirect back inside that window. Short enough
# that a stolen state isn't useful; long enough that a slow consent
# (account picker, "I'm not Alice" detour, MFA prompt) still works.
_STATE_TTL_SECONDS = 10 * 60

# 16-byte nonce baked into the state envelope. Pure entropy: it
# makes two states for the same user at the same second still
# distinct, so a passive observer can't link them.
_STATE_NONCE_BYTES = 16

# Number of dot-delimited fields in the state envelope:
# ``user_id.nonce.issued_at.signature``. Pinned as a constant so the
# parser's length check reads as intent, not a magic 4.
_STATE_FIELDS = 4


# ----- exceptions --------------------------------------------------------


class GmailOAuthError(Exception):
    """Base for any Gmail OAuth failure path."""


class GmailNotConfiguredError(GmailOAuthError):
    """The OAuth client id / secret env vars are unset.

    Surfaced as its own type so the route can return a targeted 503
    ("integration not wired up") rather than a generic 500.
    """


class OAuthStateError(GmailOAuthError):
    """The ``state`` parameter is missing, malformed, or expired."""


class TokenExchangeError(GmailOAuthError):
    """Google's token endpoint rejected the code or refused to mint tokens."""


# ----- state signing -----------------------------------------------------


def _sign_state(user_id: UUID, *, nonce: str, issued_at: int, secret: str) -> str:
    """HMAC-sign ``(user_id, nonce, issued_at)`` and return the urlsafe envelope.

    Format: ``<user_id>.<nonce>.<issued_at>.<hex_hmac>``. Dot-delimited
    rather than JSON+base64 so the URL stays short and the parser is
    a single ``str.split``. The HMAC covers the first three fields.
    """
    payload = f"{user_id}.{nonce}.{issued_at}"
    mac = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()
    return f"{payload}.{mac}"


def _verify_state(state: str, *, secret: str, now: float | None = None) -> UUID:
    """Validate ``state`` and return the encoded user id.

    Raises :class:`OAuthStateError` on every failure path: malformed
    envelope, bad HMAC, stale timestamp, unparsable user id. ``now``
    is injectable for tests; production callers omit it.
    """
    parts = state.split(".")
    if len(parts) != _STATE_FIELDS:
        raise OAuthStateError("malformed state")

    user_id_raw, nonce, issued_at_raw, signature = parts

    payload = f"{user_id_raw}.{nonce}.{issued_at_raw}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        # Constant-time compare. A casual ``==`` would leak the
        # matching prefix length over enough probes.
        raise OAuthStateError("state signature mismatch")

    try:
        issued_at = int(issued_at_raw)
    except ValueError as exc:
        raise OAuthStateError("malformed state timestamp") from exc

    actual_now = now if now is not None else time.time()
    if actual_now - issued_at > _STATE_TTL_SECONDS:
        raise OAuthStateError("state expired")
    if issued_at - actual_now > _STATE_TTL_SECONDS:
        # Future-dated state — clock skew or forgery. Reject either
        # way; legitimate callers won't trip this.
        raise OAuthStateError("state issued in the future")

    try:
        return UUID(user_id_raw)
    except ValueError as exc:
        raise OAuthStateError("malformed state subject") from exc


# ----- consent URL -------------------------------------------------------


def build_consent_url(user_id: UUID, *, now: float | None = None) -> str:
    """Build Google's authorization URL for the given user.

    ``access_type=offline`` is what unlocks the refresh token —
    without it Google only mints a 1-hour access token with no way
    to silently renew. ``prompt=consent`` forces the consent screen
    even on a re-grant so Google reissues a refresh token; without
    it, a re-grant returns only the access token and we lose the
    cursor we need for Pub/Sub push.
    """
    settings = get_settings()
    if not settings.gmail_oauth_client_id:
        raise GmailNotConfiguredError("GMAIL_OAUTH_CLIENT_ID is not set")

    issued_at = int(now if now is not None else time.time())
    nonce = secrets.token_hex(_STATE_NONCE_BYTES)
    state = _sign_state(user_id, nonce=nonce, issued_at=issued_at, secret=settings.jwt_secret)

    params = {
        "client_id": settings.gmail_oauth_client_id,
        "redirect_uri": settings.gmail_oauth_redirect_uri,
        "response_type": "code",
        "scope": GMAIL_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        # ``include_granted_scopes`` lets a future scope expansion
        # incrementally extend an existing grant rather than re-
        # prompting the user from scratch. Cheap to ship now.
        "include_granted_scopes": "true",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


# ----- token exchange ----------------------------------------------------


@dataclass(frozen=True)
class TokenExchangeResult:
    """The fields we keep from a successful code-for-tokens swap."""

    refresh_token: str
    access_token: str
    google_email: str


async def exchange_code(
    *,
    code: str,
    state: str,
    client: httpx.AsyncClient,
    now: float | None = None,
) -> tuple[UUID, TokenExchangeResult]:
    """Verify ``state``, exchange ``code`` for tokens, fetch the Google email.

    Returns ``(user_id, tokens)``. Raises :class:`OAuthStateError` on
    a bad state, :class:`TokenExchangeError` on a failed token-
    endpoint or userinfo call, and :class:`GmailNotConfiguredError`
    if the OAuth client isn't wired up.
    """
    settings = get_settings()
    if not (settings.gmail_oauth_client_id and settings.gmail_oauth_client_secret):
        raise GmailNotConfiguredError("Gmail OAuth client credentials are not set")

    user_id = _verify_state(state, secret=settings.jwt_secret, now=now)

    token_resp = await client.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.gmail_oauth_client_id,
            "client_secret": settings.gmail_oauth_client_secret,
            "redirect_uri": settings.gmail_oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
    )
    if token_resp.status_code != httpx.codes.OK:
        # Don't surface Google's body to the client — it can include
        # the rejected ``client_secret`` reflected back. Log it for
        # ops, return an opaque error to the user.
        log.warning(
            "gmail_oauth.token_exchange_failed",
            status=token_resp.status_code,
            body=token_resp.text[:500],
        )
        raise TokenExchangeError(f"token exchange failed: HTTP {token_resp.status_code}")

    body = token_resp.json()
    refresh_token = body.get("refresh_token")
    access_token = body.get("access_token")
    if not (isinstance(refresh_token, str) and isinstance(access_token, str)):
        # ``prompt=consent`` should guarantee a refresh token, but
        # Google occasionally drops it on a non-consenting re-grant.
        # Treat as an exchange failure so the user re-runs the flow.
        log.warning("gmail_oauth.token_response_missing_fields", keys=list(body.keys()))
        raise TokenExchangeError("token response missing refresh_token or access_token")

    userinfo_resp = await client.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_resp.status_code != httpx.codes.OK:
        log.warning(
            "gmail_oauth.userinfo_failed",
            status=userinfo_resp.status_code,
            body=userinfo_resp.text[:500],
        )
        raise TokenExchangeError(f"userinfo failed: HTTP {userinfo_resp.status_code}")

    userinfo = userinfo_resp.json()
    google_email = userinfo.get("email")
    if not isinstance(google_email, str) or not google_email:
        raise TokenExchangeError("userinfo missing email")

    return user_id, TokenExchangeResult(
        refresh_token=refresh_token,
        access_token=access_token,
        google_email=google_email,
    )


# ----- revocation --------------------------------------------------------


async def revoke_refresh_token(
    *,
    refresh_token: str,
    client: httpx.AsyncClient,
) -> bool:
    """Best-effort revoke a refresh token at Google's endpoint.

    Returns ``True`` on success, ``False`` on any failure. The local
    delete is the source of truth — Google's revocation is a courtesy
    that closes the access token on their side too. We never raise
    from here; a network blip during DELETE shouldn't strand the row.
    """
    try:
        resp = await client.post(
            GOOGLE_REVOKE_URL,
            data={"token": refresh_token},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        log.warning("gmail_oauth.revoke_network_error", error=str(exc))
        return False

    # Google returns 200 on success and 400 if the token was already
    # revoked / never valid. Treat 400 as "already gone, fine".
    if resp.status_code in (httpx.codes.OK, httpx.codes.BAD_REQUEST):
        return True
    log.warning("gmail_oauth.revoke_unexpected_status", status=resp.status_code)
    return False

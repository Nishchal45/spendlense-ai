"""Gmail OAuth + Pub/Sub endpoints — Phase 5.6 PRs B + C.

Five routes:

* ``GET  /integrations/gmail/connect`` — auth required. Returns the
  Google consent URL for the current user. The frontend redirects
  the browser to it.
* ``GET  /integrations/gmail/callback`` — **no bearer auth**. The
  signed ``state`` parameter is the authenticator. Exchanges the
  code, encrypts the refresh token, upserts a ``gmail_connections``
  row, and 302s the browser to ``/receipts?gmail=connected``.
* ``GET  /integrations/gmail`` — auth required. Lists the user's
  connections. Tokens are never in the response.
* ``DELETE /integrations/gmail/{id}`` — auth required. Deletes the
  row and best-effort revokes the refresh token at Google's
  endpoint.
* ``POST /integrations/gmail/push`` — **no bearer auth**. Google
  Cloud Pub/Sub signs every push with an OIDC ID-token JWT; the
  JWT is the authenticator. Verified body decodes to a Gmail
  notification, which we resolve to a ``gmail_connections`` row by
  ``emailAddress`` and hand off to a Celery task.

Why the frontend redirect URL is hardcoded to a relative path: the
API and the frontend share an origin in the deploy (reverse-proxy
pattern). A configurable redirect target is a future deploy concern;
shipping it now would invite "redirect to attacker's URL" CSRF
amplifications without buying anything.
"""

from __future__ import annotations

from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.v1.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.secret_box import (
    SecretBoxError,
    SecretBoxNotConfiguredError,
    decrypt_secret,
)
from app.schemas.gmail_connection import (
    GmailConnectionList,
    GmailConnectionOut,
    GmailConnectURL,
)
from app.services.gmail_connection_service import (
    GmailConnectionNotFoundError,
    delete_connection,
    find_by_google_email,
    get_connection,
    list_connections,
    upsert_connection,
)
from app.services.gmail_oauth_service import (
    GmailNotConfiguredError,
    OAuthStateError,
    TokenExchangeError,
    build_consent_url,
    exchange_code,
    revoke_refresh_token,
)
from app.services.gmail_pubsub_service import (
    PushNotConfiguredError,
    PushVerificationError,
    verify_push_request,
)

router = APIRouter(prefix="/integrations/gmail", tags=["integrations"])
log = structlog.get_logger()


# Where the browser lands after the callback finishes. Two query
# params signal the outcome to the frontend so it can show a toast:
# ``?gmail=connected`` on success, ``?gmail=error&reason=...`` on a
# failure that's worth surfacing. The path is relative — the
# browser resolves it against the API host, which in the production
# reverse-proxy setup is the same origin as the SPA.
_FRONTEND_SUCCESS_PATH = "/receipts?gmail=connected"


def _frontend_error(reason: str) -> str:
    return f"/receipts?gmail=error&reason={reason}"


def _service_unavailable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gmail connection not found")


# Default httpx client builder. Tests patch this to inject a
# ``MockTransport`` instead of stubbing every call site. Keeping it
# a module-level function (not a singleton) means each request gets
# its own connection pool — the OAuth flow is far too low-traffic
# for the pool reuse to matter, and per-request clients are easier
# to reason about under teardown.
def _build_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


@router.get("/connect", response_model=GmailConnectURL)
async def connect(current_user: CurrentUser) -> GmailConnectURL:
    """Build the Google consent URL for the current user."""
    try:
        url = build_consent_url(current_user.id)
    except GmailNotConfiguredError as exc:
        log.warning("gmail.connect_not_configured")
        raise _service_unavailable("Gmail integration is not configured") from exc
    return GmailConnectURL(url=url)


# Map every typed callback failure to the ``?reason=`` code the
# frontend renders. ``SecretBoxNotConfiguredError`` and the explicit
# config-check both collapse to the same ``not_configured`` reason
# so the frontend tells one story for "integration not wired up".
_CALLBACK_ERROR_REASONS: dict[type[Exception], str] = {
    OAuthStateError: "bad_state",
    GmailNotConfiguredError: "not_configured",
    TokenExchangeError: "exchange_failed",
    SecretBoxNotConfiguredError: "not_configured",
    SecretBoxError: "encryption_failed",
}


@router.get("/callback", include_in_schema=False)
async def callback(
    session: SessionDep,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Handle Google's redirect-back. Exchange the code, persist tokens, redirect."""
    settings = get_settings()
    if not (
        settings.gmail_oauth_client_id
        and settings.gmail_oauth_client_secret
        and settings.gmail_token_encryption_key
    ):
        log.error("gmail.callback_not_configured")
        redirect_url: str = _frontend_error("not_configured")
    else:
        redirect_url = _FRONTEND_SUCCESS_PATH
        try:
            async with _build_http_client() as client:
                user_id, tokens = await exchange_code(code=code, state=state, client=client)
            await upsert_connection(
                session,
                user_id=user_id,
                google_email=tokens.google_email,
                refresh_token=tokens.refresh_token,
            )
        except tuple(_CALLBACK_ERROR_REASONS.keys()) as exc:
            reason = _CALLBACK_ERROR_REASONS[type(exc)]
            log.warning("gmail.callback_failed", reason=reason, error=str(exc))
            redirect_url = _frontend_error(reason)

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


@router.get("", response_model=GmailConnectionList)
async def list_(current_user: CurrentUser, session: SessionDep) -> GmailConnectionList:
    rows = await list_connections(session, user_id=current_user.id)
    return GmailConnectionList(items=[GmailConnectionOut.model_validate(r) for r in rows])


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    connection_id: UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> Response:
    """Delete the row and best-effort revoke the refresh token at Google."""
    # Fetch the row first so we can read the encrypted token before
    # deleting it. Couldn't do this with a single ``DELETE ...
    # RETURNING`` because we want the explicit 404 path.
    try:
        connection = await get_connection(
            session, user_id=current_user.id, connection_id=connection_id
        )
    except GmailConnectionNotFoundError as exc:
        raise _not_found() from exc

    refresh_token: str | None
    try:
        refresh_token = decrypt_secret(connection.encrypted_refresh_token)
    except SecretBoxError as exc:
        # Tampered or wrong-key ciphertext. Drop the row anyway —
        # the user clearly wants the connection gone, and a row we
        # can't decrypt is dead weight. Skip the Google revoke.
        log.warning(
            "gmail.disconnect_decrypt_failed",
            connection_id=str(connection_id),
            reason=str(exc),
        )
        refresh_token = None

    await delete_connection(session, user_id=current_user.id, connection_id=connection_id)

    if refresh_token is not None:
        async with _build_http_client() as client:
            ok = await revoke_refresh_token(refresh_token=refresh_token, client=client)
            if not ok:
                # Local row is already gone; Google will GC the grant
                # eventually. Logged at info, not warn — common cause
                # is the user already revoked from myaccount.google.com.
                log.info(
                    "gmail.disconnect_remote_revoke_skipped",
                    connection_id=str(connection_id),
                )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push", include_in_schema=False)
async def push(
    request: Request,
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> Response:
    """Receive a Pub/Sub push delivery for a Gmail mailbox change.

    Auth model: the JWT in ``Authorization: Bearer`` is the
    authenticator (verified inside :func:`verify_push_request`); the
    Pub/Sub message body is then trusted to carry the user's email
    address and Gmail history cursor.

    Why we always return 200 once the JWT verifies, even when no
    matching connection exists: Pub/Sub treats any non-2xx as a
    delivery failure and retries with exponential backoff. Returning
    404 for "we don't know that email" would burn Google's retry
    quota on a user who has already disconnected. We log + 200.
    """
    raw_body = await request.body()

    try:
        message = verify_push_request(
            authorization_header=authorization,
            raw_body=raw_body,
        )
    except PushNotConfiguredError as exc:
        # Push subscription wired up but env vars missing. 503 so
        # the misconfiguration is visible in Pub/Sub's dead-letter
        # metrics rather than masquerading as an auth failure.
        log.error("gmail.push_not_configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gmail push is not configured",
        ) from exc
    except PushVerificationError as exc:
        log.warning("gmail.push_unauthorized", reason=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Push authentication failed",
        ) from exc

    connections = await find_by_google_email(session, google_email=message.email_address)
    if not connections:
        # Common cause: the user disconnected on our end but their
        # Gmail watch hasn't been torn down yet. Log + ack so
        # Pub/Sub stops retrying.
        log.info(
            "gmail.push_no_matching_connection",
            email_address=message.email_address,
            pubsub_message_id=message.pubsub_message_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Local import to dodge a real circular: the task module
    # imports our settings + sync service, which transitively pull
    # endpoint registration. The local import keeps router import
    # ordering clean.
    from app.tasks.gmail_history_sync import gmail_history_sync

    for connection in connections:
        gmail_history_sync.delay(str(connection.id), message.history_id)

    log.info(
        "gmail.push_accepted",
        email_address=message.email_address,
        history_id=message.history_id,
        pubsub_message_id=message.pubsub_message_id,
        connections=len(connections),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

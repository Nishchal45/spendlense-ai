# ADR-0009: Gmail OAuth + Pub/Sub push ingestion

- **Status**: Accepted
- **Date**: 2026-05-02
- **Deciders**: backend, frontend, product

## Context

ADR-0002 picked Gmail OAuth as the second zero-touch ingestion
channel — the answer for users who don't want to set up a forward
rule and want SpendLens to read receipts the moment they arrive.
This ADR records the load-bearing decisions made shipping it
(PRs #37–#40). Eight questions had to land together:

1. **OAuth flow shape.** PKCE or authorization-code with secret?
2. **Refresh-token storage.** A token that grants read access to a
   user's mailbox can't sit in plaintext.
3. **OAuth callback auth.** The callback has no session cookie and
   no bearer header. What proves it's not a CSRF?
4. **Push transport.** Pub/Sub or polling? If push, how do we
   verify a delivery is genuine?
5. **Google client surface.** `google-api-python-client` or roll
   our own thin REST calls?
6. **Receipt creation path.** Do we fork a Gmail-specific receipt
   service, or reuse the inbound-email pipeline from ADR-0008?
7. **Sync semantics.** Full mailbox scan, since-last-message, or
   Gmail's `historyId` cursor?
8. **What about the `users.watch` lifecycle?** Gmail watches
   expire every 7 days. Where does renewal live?

Same constraints as the rest of the project: self-hosted-first,
Postgres-only, optional AI deps, deferred complexity.

## Decision

**Web Server OAuth flow** with **HMAC-signed `state` envelopes**
authenticating the callback, **Fernet-encrypted refresh tokens**
at rest, **Cloud Pub/Sub push** with **`google-auth` JWT
verification** as the auth boundary, **`httpx` for every other
Google REST call**, **incremental sync via `historyId`**, and
**reuse of `inbound_email_service.process_inbound_email` from
ADR-0008** as the canonical receipt-creation path. `users.watch`
renewal is explicitly deferred to Phase 8.

Every choice in detail:

### OAuth flow: Web Server, not PKCE

The flow is **authorization-code with client secret** —
"Web Server" in Google's parlance. We **deliberately don't use PKCE**.

PKCE is the right call for a *public client* (SPA-only, no
backend) where embedding a client secret would mean shipping it
to every browser. We have a backend; the secret stays on the
server; PKCE buys us nothing here. More importantly:

- **Refresh token must persist server-side.** The Pub/Sub push
  worker fires hours after the user closed the browser tab and
  needs to refresh access tokens behind the user's back. PKCE's
  short-lived single-use authorization code, replayed by a SPA,
  doesn't solve that problem.
- **Refresh tokens require `access_type=offline`.** Combined with
  `prompt=consent` to force a fresh refresh-token issue on every
  consent. Without `prompt=consent`, Google silently elides the
  refresh token on a re-grant and the existing one continues to
  work — fine for steady-state, broken for token rotation.
- **`include_granted_scopes=true`** lets a future scope expansion
  (e.g. adding Drive read-only for receipt PDFs in Drive) extend
  an existing grant incrementally rather than re-prompting from
  scratch. Cheap to ship, expensive to bolt on later.

Scope is `gmail.readonly` — the smallest scope that lets us read
message bodies and attachments. We never request `gmail.modify`:
we don't need to mark messages read, move them, or delete them,
and asking for it would double the consent screen's friction.
`gmail.metadata` is too narrow (no body, no attachments).

### Refresh-token storage: Fernet (AES-128-CBC + HMAC-SHA256)

Tokens are stored encrypted at rest via
`app.core.secret_box.encrypt_secret`, which wraps
`cryptography.fernet`. The threat model: a database backup or a
compromised SQL read should not expose long-lived refresh tokens
that grant access to the user's mailbox.

Why Fernet specifically:

- **Authenticated encryption.** Tampering fails at decrypt time —
  we don't have to roll HMAC-then-decrypt.
- **Versioned ciphertext format.** A future migration to
  ChaCha20-Poly1305 is a parser change.
- **`cryptography` is the canonical Python crypto lib.** Rolling
  our own AES-CBC would be reinvention.

The DB column is `Text`, not a fixed length, so a future
ciphertext-format upgrade doesn't need a schema migration.
`GMAIL_TOKEN_ENCRYPTION_KEY` lives in env, never in the DB. Key
rotation is a re-encrypt migration we'll write when (if) needed.

The `gmail_connections` row is upserted via Postgres'
`INSERT … ON CONFLICT (user_id, google_email) DO UPDATE` so a
**re-grant of the same Google account replaces the encrypted
token** rather than failing on the unique constraint. Re-
consenting is therefore idempotent from the user's perspective.

### OAuth callback auth: HMAC-signed `state` envelope

The callback is `GET /api/v1/integrations/gmail/callback?code=&state=`
— **no bearer auth, no session cookie**. The `state` parameter is
the authenticator.

Format: `<user_id>.<nonce>.<issued_at>.<hex_hmac>`. The HMAC is
SHA-256 over the first three fields, keyed with `JWT_SECRET`.
Verification reverses the order: parse → constant-time HMAC
compare → 10-minute window check (rejects future-dated states
too, in case of clock skew or forgery) → UUID parse.

Two reasons this beats a session cookie:

- **The flow leaves and re-enters our origin.** Google's consent
  screen redirects back to our callback URL. A SameSite=Lax
  session cookie would survive that, but a SameSite=Strict
  wouldn't, and we don't want to weaken our session policy for
  one flow.
- **CSRF is closed by signature, not by cookie.** An attacker
  who tricks a logged-in user into hitting our callback without
  a state can't mint a valid HMAC without `JWT_SECRET`.

Format choice: dot-delimited rather than JSON+base64. The URL
stays short and the parser is a single `str.split` — small wins
that keep the callback's request line from getting unwieldy.

The 10-minute window is short enough that a captured state isn't
useful, long enough that a slow consent (account picker, MFA
prompt, "I'm not Alice" detour) still works.

Failure paths route through a single redirect to
`/receipts?gmail=error&reason=...` rather than an HTTP error
status — Google's consent screen is the user's last stop, so a
404 from our callback would be a hostile dead-end. The frontend
banner reads the `reason` and surfaces a human message.

### Push transport: Cloud Pub/Sub, JWT-verified

Gmail emits `users.history.list`-relative change notifications
through Cloud Pub/Sub when `users.watch` is active. Our endpoint
is `POST /api/v1/integrations/gmail/push`. **No bearer auth** —
the OIDC ID-token JWT Google signs every push with is the
authenticator.

Verification order, in `gmail_pubsub_service.verify_push_request`:

1. JWT signature against Google's published certs (via
   `google-auth`'s `id_token.verify_oauth2_token`).
2. `aud` claim matches `GMAIL_PUBSUB_AUDIENCE` (typically the
   push URL itself). Pub/Sub injects this; mismatching means
   "this delivery wasn't meant for our subscription".
3. `iss` claim is `https://accounts.google.com`. Pinned, not
   configurable — anything else is by definition not Google.
4. `email` claim matches `GMAIL_PUBSUB_SERVICE_ACCOUNT`. The
   service account configured on the push subscription. A
   different service account = not our subscription.

Mismatches at any step return a single 401 with reason logged
but not surfaced to the caller — same fingerprint-resistance
principle as the inbound webhook.

Rejected: polling. A worker that hits `users.history.list` on
a cron would burn quota for users with no new mail, and would
have a latency floor equal to the cron interval. Push is free
on Google's side and gives second-scale ingestion latency.

Rejected: Gmail watches without push (basic OAuth + cron). Same
quota waste as polling, plus the watch lifecycle is the same
either way.

### Google client surface: `httpx` over `google-api-python-client`

We use **`httpx` for every Google REST call**: token exchange,
userinfo, revocation, `users.history.list`, `users.messages.get`,
`users.messages.attachments.get`. The only Google-Python
dependency is `google-auth`, used **strictly for the Pub/Sub JWT
verification**.

Why not `google-api-python-client`:

- **30 MB of generated stubs** for an API surface we use **six
  endpoints of**. Disk is cheap; image size and attack surface
  aren't.
- **Sync-only.** The async wrapper `aiogoogle` exists but it's a
  separate community library; the official client doesn't ship
  async support. Our worker is async-native.
- **Generated discovery layer adds a runtime fetch** for the
  Gmail API spec on first use. That's another network call we
  don't need.

`httpx` is already a dependency (we use it for the inbound-email
adapters). Six endpoints in `gmail_sync_service` cover the whole
surface — token refresh, history paginated, message fetch,
attachment fetch — each ~20 lines. Tests use `httpx.MockTransport`
to simulate Google with no network.

For the JWT verification specifically: `google-auth` ships
`google.auth.transport.requests` (needs `requests`) and
`google.auth.transport.urllib3` (needs `urllib3`, already a
transitive dep). We use the **`urllib3` transport, lazy-imported
inside `_default_verifier()`**, so module load doesn't depend on
the transport's runtime requirements. The verifier is a closure
that **binds a single `urllib3.PoolManager`-backed transport**
across calls so `google-auth`'s internal JWKS cache survives —
re-creating the transport per request would just churn TLS
handshakes.

### Receipt creation path: reuse `process_inbound_email`

A Gmail message with a receipt PDF attached is, semantically, the
same shape as a forwarded email with the same attachment. We
**lift Gmail attachments into the `InboundEmail` schema from
ADR-0008** and call `process_inbound_email` unchanged.

The synthesis happens in `gmail_sync_service._build_inbound_email`:

- `message_id` = Gmail's message id (unique per user, drives
  dedup against `receipts.external_message_id`).
- `to` = `receipts+<inbox_token>@<inbox_email_domain>` — the
  user's *own* forwarding address, synthesised from their stored
  inbox token. Gmail's actual `To:` header is the user's Gmail
  address, which is useless for our user-resolution regex. The
  synthesised value lets the existing inbound resolver work
  unchanged.
- `sender` / `subject` = the relevant Gmail headers.
- `attachments` = parts whose MIME is in the allowlist with bytes
  fetched via `users.messages.attachments.get` and re-encoded as
  standard base64.

Three benefits this gives us:

- **One canonical receipt path.** Dedup, magic-byte sniff,
  S3-first write, OCR enqueue, retry semantics — all the same
  code that ADR-0008's tests already lock in.
- **New ingestion sources are thin adapters.** SMS-to-receipt
  (Phase 8 mobile) and Slack-bot ingestion (future) become
  ~50-line bridges into `InboundEmail`. No fork, no drift.
- **Tests of the inbound-email service double as Gmail-pipeline
  tests** for everything from MIME validation onward.

Body-only Gmail messages (Uber's "Your ride is on the way",
DoorDash confirmations) are deferred to Phase 6+, same as the
forward-to-email channel. Body parsing needs an LLM round-trip
or HTML→PDF rasterisation; both belong in a separate ADR when we
ship them.

### Sync semantics: incremental `historyId` cursor

`gmail_connections.last_history_id` is the cursor. Each push
delivers Gmail's current `historyId`; the worker fetches
`users.history.list?startHistoryId=<our cursor>` and walks the
delta.

**First-push behaviour: seed and skip.** When `last_history_id`
is NULL (immediately after consent, before the first delta), we
can't compute a delta — there's nothing to compare against. We
**seed the cursor from the push's own `historyId` and process no
messages on this delivery**. Gmail's docs explicitly recommend
this pattern for the bootstrap case: the first push announces a
state we have no baseline for. The next push, with the cursor
primed, processes normally.

**Cursor advances after successful processing.** A crash mid-sync
replays the same delta on the next push — Gmail's history
records are stable, so re-processing is dedup-safe. Advancing
before processing would silently drop messages on a worker
crash.

**`users.history.list` 404 = retention overflow.** Gmail keeps
~7 days of history. A cursor older than that returns 404; we log
once, return an empty list to the caller, and **advance the
cursor to the push's `historyId`** so we resync from a fresh
anchor. Better than retrying forever against a permanent failure.

**Pagination via `nextPageToken`.** Each `users.history.list`
response can be partial; we collect message ids across pages,
deduping via insertion-ordered `dict` keys (the same id can
appear in multiple history entries, e.g. message-added followed
by label-added).

We **only consume `messagesAdded` records.** `labelAdded`,
`labelRemoved`, and `messageDeleted` aren't relevant to a receipt
pipeline — we want exactly the new-mail signal.

Rejected: full mailbox scan via `users.messages.list` on a cron.
Quota-prohibitive for users with tens of thousands of historical
messages, and the latency floor would be the cron interval.

Rejected: a since-last-message timestamp cursor. Gmail's history
is the canonical incremental API; building our own around
message timestamps would re-implement what `historyId` already
solves, and would race against email retroactively imported
into the inbox.

### Status code map: each path tells a different story

Push endpoint:

- **`204`** on every JWT-verified delivery — even with no
  matching connection. Pub/Sub treats non-2xx as a retry
  signal; returning 404 for "we don't know that email" would
  burn Google's retry quota on a user who already disconnected.
- **`401`** on JWT failure (missing/malformed/empty bearer, bad
  signature, wrong issuer, wrong audience, wrong service
  account). One reason code so an attacker can't fingerprint
  which check tripped them.
- **`503`** when `GMAIL_PUBSUB_AUDIENCE` /
  `GMAIL_PUBSUB_SERVICE_ACCOUNT` are unset. Visible
  misconfiguration vs. silent 401s on every push.

Connect / callback / list / disconnect endpoints follow the rest
of the API's conventions — auth required → 401, owner-scoped
404 on cross-tenant, 503 when env vars unset.

### `users.watch` lifecycle: deferred

Gmail's watch subscription expires every **7 days**; if it's not
renewed, Pub/Sub stops getting notifications and our pipeline
goes silent until the user reconnects. The renewal is a
straightforward "call `users.watch` weekly" cron job.

We **deliberately don't ship the renewal in this phase.** The
self-hoster bringing up Gmail integration runs `users.watch`
manually (or wires a one-line cron) for the initial bring-up;
Phase 8 will add a Celery beat schedule that walks every
connection and renews watches expiring within 24 hours. A
`watch_expiration` column on `gmail_connections` already exists
in the schema for that future cron to read.

Shipping the renewal now would mean introducing Celery beat (a
separate process) into the dev-stack just to make this phase
work end-to-end — too much surface for what is, today, an
operational concern.

### Frontend: full-window navigation, redirect-back signaling

The "Connect Gmail" button **navigates the whole window** to the
consent URL via `window.location.assign`. We considered popups
and rejected them:

- **iOS Safari blocks them aggressively.** A flow that works on
  desktop and breaks on mobile is worse than no flow.
- **MFA / account picker chains** are unpredictable in a popup —
  a user might land in a "stay signed in" prompt, or a "switch
  to your work account" detour, none of which behave well in a
  cross-window context.
- **Back button as a true affordance.** A full-window navigate
  means hitting Back returns the user to the receipts page from
  any consent stage. Cancelling the flow is intuitive.

The callback signals back via **URL query params** —
`?gmail=connected` for success, `?gmail=error&reason=...` for
failure. The `GmailRedirectBanner` component reads them on
mount, surfaces a banner, **invalidates the connections query**
so the new row appears, then **strips the params with
`setSearchParams(..., {replace: true})`** so a refresh doesn't
re-show the notice. Replace (not push) so the user doesn't have
to back through it.

Reason codes documented at the endpoint:

| Reason             | Means                                                |
| ------------------ | ---------------------------------------------------- |
| `bad_state`        | State envelope expired, malformed, or wrong HMAC.    |
| `exchange_failed`  | Google's token endpoint rejected the code.          |
| `not_configured`   | OAuth client / encryption key env vars unset.       |
| `encryption_failed`| Refresh-token Fernet encrypt step failed.           |

The frontend maps unknown reasons to a generic fallback so a
future endpoint addition doesn't render an empty banner.

## Consequences

### Positive

- **True zero-touch ingestion for Gmail users.** Connect once,
  receipts arrive on their own. The killer feature for users
  who can't be bothered to set up a forward rule.
- **Push, not poll.** Sub-second ingestion latency once a
  receipt hits the user's inbox. Quota usage scales with
  *new mail volume*, not user count.
- **Single canonical receipt path.** Forward-to-email,
  Gmail-OAuth, and (future) SMS / Slack channels all funnel
  through `process_inbound_email`. Adding a new source is a
  ~50-line adapter, not a new pipeline.
- **`google-api-python-client` avoided.** 30 MB of generated
  stubs not added to the worker image; no async wrapper
  community-lib in the dependency graph.
- **Idempotent re-grant.** A user re-running the connect flow
  for the same Google account replaces the row's encrypted
  token; we don't accumulate stale `gmail_connections` rows.
- **Refresh tokens encrypted at rest.** A leaked DB backup
  doesn't expose mailbox-scoped credentials.
- **CSRF-closed callback.** No session-cookie dependency, no
  reduction in session-cookie strictness elsewhere.

### Negative

- **`users.watch` renewal not shipped.** Self-hoster wiring is
  required to keep the push subscription alive past 7 days.
  Mitigated by the deferred Phase 8 Celery beat job and the
  `watch_expiration` column already in place.
- **Gmail-only.** Outlook / iCloud / Fastmail need either IMAP
  IDLE (different protocol, different push semantics) or
  Microsoft Graph (different OAuth surface). Phase 5.6's
  scope is Gmail; the other providers are explicit ADR
  follow-ups.
- **Body-only Gmail messages don't ingest.** Same gap as
  forward-to-email — Uber, DoorDash, and many B2B confirmations
  embed receipts in HTML, not attachments. Phase 6+ adds body
  parsing across both channels in a single PR.
- **Refresh tokens are long-lived bearers.** A user who exposes
  their `gmail_connections` row's ciphertext *and* the
  `GMAIL_TOKEN_ENCRYPTION_KEY` would expose a write-surface
  into Google's API. Mitigated by: encrypting at rest,
  separating key from DB, supporting per-row revoke at Google
  on disconnect.
- **No anti-abuse rate limit on push.** A misbehaving Gmail
  account that fires hundreds of pushes/sec could overwhelm
  the worker. Mitigated by Pub/Sub's own delivery rate (modest)
  and Celery's `worker_prefetch_multiplier=1`. An explicit
  per-user quota is a Phase 8 hardening item.
- **Per-task DB engine.** Every Celery task opens and disposes
  its own asyncpg engine — necessary because asyncpg
  connections are loop-bound, but adds connection setup
  overhead per push. Sub-millisecond at our scale; revisitable
  if the push rate ever justifies a worker-managed pool.

### Follow-ups

- **`users.watch` renewal cron.** Phase 8: Celery beat job
  walking connections, calling `users.watch` for any expiring
  within 24h, updating `watch_expiration`.
- **Outlook + iCloud + Fastmail (IMAP IDLE).** Same canonical
  `InboundEmail` shape, different transport. Separate ADR.
- **Gmail body-only parsing.** HTML → PDF rasterisation, then
  the existing OCR path. Or LLM-direct extraction from
  `body_html`. Same decision shared with the inbound-email
  channel; one ADR covers both.
- **Per-user Gmail-push rate limiting.** Phase 8 abuse
  hardening.
- **Refresh-token rotation at scale.** Right now, key rotation
  requires a re-encrypt migration. A two-key (old + new)
  ratchet for online rotation lands when ops volume justifies
  it.

## Alternatives considered

### Polling Gmail on a cron, no Pub/Sub

Rejected. Even at 5-minute cadence, that's 288 calls per user
per day on `users.history.list`. Push is free on Google's side,
gives sub-second latency, and only fires when there's actually
new mail. The Pub/Sub setup is a one-time operational cost.

### `google-api-python-client` for the REST surface

Rejected. The library is sync-only; the async wrapper
`aiogoogle` is a community shim that adds a separate dependency
graph; both pull tens of MBs of generated stubs for an API
surface six endpoints wide. Six endpoints in `httpx` keeps the
test fakes obvious (`MockTransport` is one line) and the worker
image small.

### `requests` transport for `google-auth`

Rejected. Adds `requests` to the dependency tree for a single
caller (the JWT verifier). `urllib3` is already pulled in by
`httpx`; using its transport keeps the dep graph flat.

### A Gmail-specific `Receipt` creation service

Rejected. Forward-to-email's `process_inbound_email` already
owns dedup + MIME validation + S3-first write + OCR enqueue.
Forking it would either drift over time or require a refactor
to merge them later. Synthesising the `InboundEmail` shape
from a Gmail message is ~20 lines and reuses everything else.

### Storing Gmail message bodies, not just attachments, in MinIO

Considered. Body parsing is genuinely useful for receipt
sources that don't attach a PDF. But adding it to the same PR
would mean: HTML-to-PDF dependency (Playwright or wkhtmltopdf),
or LLM-direct body extraction (extra prompt-engineering surface),
or both. Each is large enough to want its own ADR. Phase 5.6
explicitly stays attachment-only — the high-value receipts
(Uber, Airlines, B2B) all attach.

### Polling-style fallback when Pub/Sub fails

Rejected for now. Pub/Sub at-least-once delivery + our retry
loop covers transient cases. A separate poller that catches up
when Pub/Sub itself is broken would double the worker logic for
a failure mode we haven't seen in practice. If we hit it,
the cron job that renews watches (Phase 8) is a natural place
to bolt on a "fetch any history since last seen" sweep.

### Storing the OAuth `state` in Redis instead of HMAC-signing it

Considered. Stateful state-store works fine but introduces a
runtime dependency for a flow that's already well-served by
HMAC. The HMAC envelope is self-contained — no Redis read on
the callback, no expiry sweep, no race condition between the
issuance and consumption sides.

### One Gmail watch per user, not per connection

Rejected. A user could connect both their personal and work
Gmail accounts. The `gmail_connections` row is per `(user,
google_email)` and Gmail's watch is per-mailbox, so the
bijection is clean. Folding multiple accounts into one row
would mean inventing our own "primary mailbox" notion that
Gmail doesn't expose.

### `prompt=select_account` instead of `prompt=consent`

Rejected. `select_account` lets the user pick which Google
account to grant from but **doesn't force a refresh-token
re-issue** on a re-grant. Over time, re-consenting users would
end up with the original refresh token still in our DB — fine
until the *original* token gets revoked (e.g. user changed
their Google password), at which point our column is dead and
the user has to re-flow anyway. `consent` forces a fresh issue
every time and keeps the column in sync with what Google
considers active.

## References

- Google OAuth 2.0 for Web Server applications:
  <https://developers.google.com/identity/protocols/oauth2/web-server>
- Gmail API push notifications + `users.watch`:
  <https://developers.google.com/gmail/api/guides/push>
- Cloud Pub/Sub push authentication (OIDC ID tokens):
  <https://cloud.google.com/pubsub/docs/authenticate-push-subscriptions>
- Gmail history-based incremental sync:
  <https://developers.google.com/gmail/api/guides/sync>
- ADR-0002 (zero-touch ingestion channels):
  [`adr/0002-receipt-ingestion-channels.md`](0002-receipt-ingestion-channels.md)
- ADR-0008 (the inbound-email pipeline this ADR reuses):
  [`adr/0008-inbound-email.md`](0008-inbound-email.md)
- `cryptography.fernet` ciphertext format:
  <https://cryptography.io/en/latest/fernet/>
- `backend/app/services/gmail_oauth_service.py`,
  `backend/app/services/gmail_pubsub_service.py`,
  `backend/app/services/gmail_sync_service.py`,
  `backend/app/services/gmail_connection_service.py`,
  `backend/app/api/v1/endpoints/integrations_gmail.py`,
  `backend/app/tasks/gmail_history_sync.py`,
  `backend/app/core/secret_box.py`,
  `backend/app/models/gmail_connection.py`,
  `backend/alembic/versions/0004_gmail_connections.py`,
  `frontend/src/api/integrations.ts`,
  `frontend/src/components/receipts/GmailConnectionsCard.tsx`,
  `frontend/src/components/receipts/GmailRedirectBanner.tsx`.

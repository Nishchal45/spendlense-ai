# Roadmap

This is the source of truth for "what ships next". The README checklist
mirrors the status column here. Dates are targets, not contracts.

## Phase 0 — Bootstrap ✅

- Repo, license, README
- Docker stack (Postgres, Redis, MinIO, API)
- CI (GitHub Actions, lint + mypy + tests + migrations)
- `docs/architecture.md`, `docs/schema.md`

## Phase 1 — Foundation ✅

- SQLAlchemy 2 async models (`users`, `expenses`, `line_items`,
  `receipts`, `budgets`, `category_corrections`)
- Async Alembic migration 0001 (5 enums + 6 tables)
- FastAPI app factory + structured logging
- `GET /health` (liveness) and `GET /health/ready` (DB + Redis probe)
- Dev tooling: pre-commit hooks, `CONTRIBUTING.md`, `SECURITY.md`

## Phase 2 — Authentication ✅ (current)

- `POST /auth/register`, `POST /auth/login`, `GET /auth/me`
- bcrypt hashing (cost 12) + HS256 JWTs (24h TTL)
- `CurrentUser` FastAPI dependency
- Email normalisation, unique-constraint race guard, constant-time
  login failures

## Phase 3 — Expenses CRUD ✅

- `POST /expenses` — create a manual expense
- `GET /expenses` — keyset-paginated list (category, date range,
  merchant ILIKE, amount range filters)
- `GET /expenses/{id}`, `PATCH /expenses/{id}`, `DELETE /expenses/{id}`
- Ownership enforced in every query — cross-tenant access returns
  404, not 403 (no existence oracle)
- `ETag` / `If-Match` on mutating routes; stale-write = 412
- Shape documented in [ADR-0003](adr/0003-expenses-api.md)

## Phase 4 — Receipt upload & storage ✅

- `POST /receipts` — multipart upload → MinIO
- `GET /receipts`, `GET /receipts/{id}`, `DELETE /receipts/{id}`
- `GET /receipts/{id}/url` — 5-minute signed S3 URL (no proxy)
- HMAC-prefixed opaque object keys; no user id on the wire
- Magic-byte MIME sniffing (JPEG/PNG/PDF/WEBP/HEIC); 10 MiB cap
- S3-first write with explicit blob cleanup on DB failure
- Shape documented in [ADR-0004](adr/0004-receipt-storage.md)

## Phase 5 — OCR + categorisation pipeline ✅

- Celery worker on Redis broker (JSON-only, late acks, soft/hard time limits)
- Two-task pipeline: `process_receipt` (Tesseract OCR + parser) →
  `categorise_receipt` (creates the Expense row)
- Categorisation chain: user corrections → static rule map →
  `gpt-4o-mini` LLM → `OTHER`. Self-hosted users without an
  OpenAI key get rules + corrections.
- GPT-4V fallback when Tesseract mean confidence drops below
  `ocr_confidence_threshold`; vision returns structured fields
  directly, no regex round-trip.
- Polling + recovery: `GET /receipts/{id}/status` and
  `POST /receipts/{id}/retry`.
- PDF support via `poppler-utils` + `pdf2image` (first page,
  200 DPI).
- Correction feedback loop: PATCHing an expense's category upserts
  into `category_corrections`; future receipts from that merchant
  use the corrected category for free.
- State machine: `uploaded → processing → parsed → categorised | failed`.
- Shape documented in [ADR-0005](adr/0005-pipeline-architecture.md).

## Phase 5.5 — Zero-touch ingestion: forward-to-email ✅

- Per-user 128-bit forwarding token (`receipts+<32-hex>@<inbox_email_domain>`)
  minted at signup via `secrets.token_hex(16)` — not derived from
  the user id, rotatable, single-index lookup
- `POST /api/v1/inbound/email` webhook with Stripe-style versioned
  HMAC-SHA256 signature (`X-SpendLens-Signature: t=<unix>,v1=<hex>`),
  5-minute replay window, constant-time compare
- Per-user dedup on `external_message_id` via partial unique index
  `(user_id, external_message_id) WHERE external_message_id IS NOT NULL`
  so manual uploads stay NULL while email-sourced rows enforce
  idempotency
- Provider-agnostic canonical `InboundEmail` payload; Postmark /
  SES Inbound / Mailgun adapters land as ADR-0008 follow-ups
- Each allowed-MIME attachment travels the same `create_receipt`
  path as direct uploads (S3-first ordering, magic-byte sniff,
  OCR enqueue)
- Forwarding address surfaced on the receipts page with one-click
  copy
- Status code map: 202 accepted (with `deduped` flag) / 400
  malformed / 401 bad signature / 404 unknown recipient / 503
  unset secret
- Shape documented in [ADR-0008](adr/0008-inbound-email.md)

## Phase 5.6 — Zero-touch ingestion: Gmail OAuth + Pub/Sub push ✅

- OAuth 2.0 Web Server flow with `gmail.readonly` scope,
  `access_type=offline`, and `prompt=consent` so every grant mints
  a fresh refresh token. PKCE rejected because we have a backend
  and the token must persist server-side
- Encrypted-at-rest refresh tokens via Fernet (AES-128-CBC + HMAC-
  SHA256); `gmail_connections` upserted on
  `(user_id, google_email)` so re-consenting is idempotent
- HMAC-signed `state` envelope (`<user_id>.<nonce>.<issued_at>.<hmac>`)
  authenticating the OAuth callback — no session cookie required,
  10-minute window, constant-time verify
- Cloud Pub/Sub push notifications with OIDC ID-token JWT
  verification (`google-auth` only for the JWT,
  `urllib3`-transported, JWKS-cached); audience pinned to push URL,
  `email` claim pinned to configured service account, issuer pinned
  to `https://accounts.google.com`
- `httpx` for every Gmail REST call (token exchange, userinfo,
  revocation, `users.history.list`, `users.messages.get`,
  `users.messages.attachments.get`) — `google-api-python-client`
  rejected to avoid 30 MB of generated stubs for six endpoints
- Incremental sync via Gmail's `historyId` cursor; first push
  seeds the cursor without processing (Gmail's recommended
  bootstrap), subsequent pushes pull deltas, cursor advances after
  successful processing so a crash mid-run replays the same delta
- 404 from `users.history.list` (cursor older than Gmail's ~7-day
  retention) handled by advancing the cursor to the push value
- Receipts created via the existing
  `inbound_email_service.process_inbound_email` from ADR-0008 —
  Gmail attachments are lifted into the canonical `InboundEmail`
  shape with a synthesised `to = receipts+<inbox_token>@…` so the
  same dedup, MIME-sniff, S3-first write, and OCR enqueue path
  applies
- Frontend `GmailConnectionsCard` + `GmailRedirectBanner` on the
  receipts page; "Connect Gmail" full-window navigates to consent,
  callback redirects back with `?gmail=connected` /
  `?gmail=error&reason=...`, banner strips the params after
  rendering and invalidates the connections query
- `users.watch` lifecycle (7-day expiry) explicitly deferred to
  Phase 8's Celery beat; `watch_expiration` column already in
  schema for the future renewal cron
- Generic IMAP IDLE for Outlook / iCloud / Fastmail deferred to a
  separate ADR follow-up
- Shape documented in [ADR-0009](adr/0009-gmail-oauth-pubsub.md)

## Phase 6 — Insights ✅

- `GET /insights/monthly` — per-category totals/counts/averages for
  one month
- `GET /insights/trends` — rolling N-month dense (month × category)
  grid for chart libraries
- `GET /insights/anomalies` — z-score detection over a rolling
  baseline with sample-size, zero-stddev, and sample-vs-population
  defenses
- `GET /budgets`, `POST/PATCH/DELETE /budgets/{id}` — CRUD with
  unique-constraint 409, soft-delete via ``active`` flag
- `GET /budgets/status` — spend-vs-budget per active monthly
  budget with green/amber/red colour state and threshold alerts
- Frontend `/insights` page with donut, stacked-bar trend chart,
  anomaly list, and budget progress bars (Recharts)
- Shape documented in [ADR-0007](adr/0007-insights-architecture.md)

## Phase 7 — Frontend ✅

- Vite 6 + React 18 + TypeScript 5 strict (with `exactOptionalPropertyTypes`,
  `verbatimModuleSyntax`, etc.)
- TanStack Query v5 for server state, React Router DOM v6 for routing,
  Tailwind v3 for styling
- Auth via an in-memory pub/sub token store; React context mirrors
  the store, API client reads it per-request. 401 auto-clears.
  httpOnly cookie + silent refresh deferred to Phase 8.
- Login + register flow with route protection
- Expenses dashboard with cursor-paginated list, debounced merchant
  search, date range, inline category edit (optimistic), modal
  create / edit via native `<dialog>`, optimistic delete
- Receipts dashboard with drag-and-drop upload, status badges keyed
  to ADR-0005's state machine, two-tier polling (list 5 s, per-row
  2 s while in flight), one-click retry on failed rows
- PWA manifest with `share_target` action — receipts shared from
  the OS share sheet (iOS Safari, Android Chrome) land in the
  upload flow. Service worker stashes the file in Cache Storage,
  the SPA route picks it up and runs the authed upload
- ESLint v9 flat config + Prettier; Vitest + Testing Library; new
  `frontend` CI job parallel to `backend`
- Shape documented in [ADR-0006](adr/0006-frontend-architecture.md)

## Phase 8 — Mobile + ship

- React Native / Expo app — camera → upload is the killer flow
- iOS share extension (the platform-sanctioned way to handle SMS
  receipts on iOS, since direct SMS reads are forbidden)
- Android SMS reader behind an opt-in toggle, gated on Play Store
  Restricted Permissions approval
- Push notifications for budget threshold trips
- Docker Compose prod profile
- Terraform + DigitalOcean droplet deploy script
- Uptime monitoring, log shipping

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

## Phase 5.5 — Zero-touch ingestion: forward-to-email

See [ADR-0002](adr/0002-receipt-ingestion-channels.md). The cheapest,
broadest receipt-ingestion channel — ships first because it works on
every email provider and doubles as the iOS SMS workaround.

- Per-user forwarding address (`receipts+<token>@inbox.spendlens.app`)
  minted on signup
- Inbound email via a managed provider webhook (Postmark / SES /
  Mailgun) with signature verification + replay protection
- `POST /api/v1/inbound/email` hands off to the same ingestion queue
  the upload endpoint uses
- Dedup on `(user_id, message_id_hash)` so re-deliveries don't
  double-book

## Phase 5.6 — Zero-touch ingestion: Gmail OAuth + generic IMAP

- Gmail API with `gmail.readonly` scope + Cloud Pub/Sub push
  notifications (no polling)
- Generic IMAP IDLE for Outlook / iCloud / Fastmail / self-hosted
- One ingestion queue across every channel — downstream OCR pipeline
  doesn't care where a message came from
- Encrypted-at-rest refresh tokens, per-user revocation flow

## Phase 6 — Insights

- Monthly breakdown, category trends
- Anomaly detection (z-score on rolling mean)
- Per-category budgets with threshold alerts

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

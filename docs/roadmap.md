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

## Phase 5 — OCR + categorisation pipeline

- Celery worker, Redis broker
- Tesseract first; GPT-4V fallback when confidence < threshold
- Feedback loop — user corrections update
  `category_corrections`

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

## Phase 7 — Frontend

- React + TypeScript + Vite
- React Query for server state, Tailwind for styling
- Auth token in memory + silent-refresh later
- PWA `share_target` manifest — receipts from any app (SMS, WhatsApp,
  Venmo, bank push) land via the browser's share sheet

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

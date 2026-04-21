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

## Phase 3 — Expenses CRUD (next)

- `POST /expenses` — create a manual expense
- `GET /expenses` — paginated list with filters (category, date range,
  merchant)
- `GET /expenses/{id}`, `PATCH /expenses/{id}`, `DELETE /expenses/{id}`
- Strong ownership checks — a user can only see their own rows
- `ETag` / `If-Match` on mutating routes to kill write races

## Phase 4 — Receipt upload & storage

- `POST /receipts` — multipart upload → MinIO
- Object-key scheme that doesn't leak user ids
- Virus/mime sniffing on upload

## Phase 5 — OCR + categorisation pipeline

- Celery worker, Redis broker
- Tesseract first; GPT-4V fallback when confidence < threshold
- Feedback loop — user corrections update
  `category_corrections`

## Phase 6 — Insights

- Monthly breakdown, category trends
- Anomaly detection (z-score on rolling mean)
- Per-category budgets with threshold alerts

## Phase 7 — Frontend

- React + TypeScript + Vite
- React Query for server state, Tailwind for styling
- Auth token in memory + silent-refresh later

## Phase 8 — Ship

- Docker Compose prod profile
- Terraform + DigitalOcean droplet deploy script
- Uptime monitoring, log shipping

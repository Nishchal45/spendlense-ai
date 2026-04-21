# SpendLens

Self-hosted expense tracker that parses receipt photos into categorised
spending, tracks budgets, and flags anomalies. Runs entirely on your own
machine — your receipts never leave your box.

[![CI](https://github.com/Nishchal45/spendlense-ai/actions/workflows/ci.yml/badge.svg)](https://github.com/Nishchal45/spendlense-ai/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

Every app in this space either requires handing over your bank credentials
to a third party or lives behind a subscription. I wanted something I can
run with `docker compose up`, where the AI bits are optional and the data
stays on disk I own.

## What it does (when finished)

- Upload a receipt photo → merchant, total, date, line items extracted
- **Zero-touch ingestion** — forward email receipts to a per-user
  address, connect Gmail for automatic inbox scanning, or share from
  any app via the mobile share sheet
- Expenses auto-categorised; corrections feed back into future predictions
- Monthly breakdown, category trends, anomaly detection
- Per-category budgets with threshold alerts
- REST API with OpenAPI docs (mobile clients plug in later)

## Current status

Under active development. Milestone checklist:

- [x] Repo, docs, dev stack
- [x] SQLAlchemy models + initial migration
- [x] `/health` and `/health/ready` probes
- [x] JWT auth (register / login / me)
- [ ] Expense CRUD with filtering and pagination
- [ ] Receipt upload to object storage
- [ ] OCR pipeline (Tesseract → GPT-4V fallback)
- [ ] LLM categorisation with correction feedback loop
- [ ] Insights engine (trends, anomalies, budgets)
- [ ] React dashboard
- [ ] CI/CD and one-command deploy

## Stack

| Layer            | Pick                        |
| ---------------- | --------------------------- |
| API              | Python 3.12, FastAPI, Pydantic |
| DB               | PostgreSQL 16 + SQLAlchemy 2 (async) + Alembic |
| Cache / broker   | Redis 7                     |
| Task queue       | Celery                      |
| Object storage   | MinIO locally (S3 in prod)  |
| OCR              | Tesseract → GPT-4V fallback |
| LLM              | OpenAI (optional)           |
| Frontend         | React + TypeScript (upcoming) |

See [`docs/architecture.md`](docs/architecture.md) for the why behind each
pick and [`docs/schema.md`](docs/schema.md) for the database design.

## Quickstart

Prereqs: Docker Desktop (or Docker Engine + Compose), `make`.

The `Makefile` autodetects Docker Compose v2 (`docker compose`) and falls back
to v1 (`docker-compose`). Either works; v2 is recommended.

```bash
git clone https://github.com/Nishchal45/spendlense-ai.git spendlens
cd spendlens

cp .env.example .env
# Edit .env and set JWT_SECRET to a long random value.

make up
make migrate
```

Check it's alive:

```bash
curl -s http://localhost:8000/api/v1/health | jq
curl -s http://localhost:8000/api/v1/health/ready | jq
```

Interactive API docs live at <http://localhost:8000/docs>. MinIO console at
<http://localhost:9001> (login with `S3_ACCESS_KEY` / `S3_SECRET_KEY`).

Host ports are deliberately bound to `127.0.0.1` and shifted off the
defaults (Postgres on `5433`, Redis on `6380`) so the dev stack doesn't
collide with host-installed services. Containers still talk to each
other on standard ports over the compose network.

## Development

```bash
make help           # list all targets
make api-shell      # bash into the API container
make db-shell       # psql into postgres
make test           # run the pytest suite
make lint           # ruff
make typecheck      # mypy
make migrate-new msg="add foo column"
```

Code style is enforced by `ruff` (formatting + lint). Type annotations are
required on all new functions; `mypy --strict` runs in CI.

## Layout

```
spendlens/
├── backend/          FastAPI service, Alembic, Celery tasks
├── frontend/         React + TypeScript SPA (scaffolded later)
├── docs/             Architecture, schema, decisions
├── docker-compose.yml
├── Makefile
└── .env.example
```

## License

MIT — see [LICENSE](LICENSE).

# Architecture

## Problem

I spend money across cards, UPI, and cash. Every month I either maintain a
spreadsheet manually or I don't — and then I have no idea where the money went.
The apps that already solve this (Expensify, Copilot, MonAi) either require me
to hand my bank credentials to a third party or lock the useful features behind
a paywall. I want a tracker I can run on my own box: snap a photo of a receipt,
have it parsed and categorised, and get honest answers about my spending.

## Scope

### In scope

- Upload a receipt image → merchant, total, date, line items extracted
- Expenses auto-categorised, with the ability to correct categories
- Corrections feed back into future categorisation (merchant memory)
- Monthly breakdown by category and trend over time
- Budgets per category with threshold alerts
- Simple anomaly detection on category spend
- Single-user, JWT-authenticated REST API
- Self-hosted via `docker compose up`

### Out of scope (for now)

- Bank account syncing / Plaid / Open Banking
- Shared / household expenses
- Bill splitting
- Mobile apps (comes later — Phase 11+)
- Multi-currency
- Investments / net worth tracking

Cutting these aggressively up front. Scope creep is how side projects die.

## High-level flow

Receipt upload is the interesting path because it touches every layer:

```
client ──▶ POST /receipts (multipart)
              │
              ▼
          api validates + stores blob in S3/MinIO
              │
              ▼
          receipt row created (status = uploaded)
              │
              ▼
          202 Accepted ──▶ client starts polling
                              GET /receipts/{id}

          meanwhile:
          celery worker picks up receipt.process task
              │
              ├─▶ OCR (Tesseract)
              │     confidence < 60? ──▶ GPT-4V fallback
              │
              ├─▶ parse text → { merchant, total, date, line_items }
              │
              ├─▶ categorise
              │     corrections cache hit? ──▶ use cached category
              │     else ──▶ LLM call ──▶ rule fallback if LLM fails
              │
              └─▶ write Expense + LineItems, status = categorised
```

Two design choices worth defending:

1. **202 and polling, not 200.** The client shouldn't block on OCR + LLM
   latency. 202 signals "accepted, not done." A websocket push would be nicer
   UX but adds infra complexity I don't need yet.
2. **OCR fallback is cost-driven.** Tesseract is free and fast, GPT-4V is
   slow and paid. Running Tesseract first keeps the AI bill proportional to
   edge cases (blurry, handwritten, angled photos), not total volume.

## Components

```
┌──────────────┐      ┌───────────────┐
│  React SPA   │◀────▶│  FastAPI API  │
└──────────────┘      └───┬───────────┘
                          │
         ┌────────────────┼───────────────┐
         │                │               │
         ▼                ▼               ▼
   ┌──────────┐     ┌──────────┐    ┌──────────┐
   │ Postgres │     │  Redis   │    │  MinIO   │
   └──────────┘     └────┬─────┘    └──────────┘
                         │
                         ▼
                   ┌──────────┐
                   │  Celery  │──── OpenAI / Tesseract
                   │  worker  │
                   └──────────┘
```

## Tech stack and why

| Layer          | Pick                | Why                                                                                                   |
| -------------- | ------------------- | ----------------------------------------------------------------------------------------------------- |
| API framework  | FastAPI             | Async-native, Pydantic validation, auto OpenAPI docs                                                  |
| Language       | Python 3.12         | Ecosystem for OCR and LLM tooling                                                                     |
| DB             | PostgreSQL 16       | Relational model fits (user → expenses → line items); aggregates and `GROUP BY` are native           |
| Cache / broker | Redis 7             | Rate limits, session data, AND Celery broker. One dep does three jobs.                                |
| Object storage | MinIO (→ S3 in prod)| S3-compatible locally so prod is a config swap                                                        |
| Task queue     | Celery              | Retries, dead-letter, scheduling — all built in                                                       |
| OCR            | Tesseract + GPT-4V  | Free for clean receipts, AI fallback for the messy 30%                                                |
| LLM            | OpenAI              | Structured JSON output, low latency on `gpt-4o-mini` for categorisation                                |
| Auth           | JWT (python-jose)   | Stateless, standard, simple                                                                           |
| Frontend       | React + TypeScript  | Type-safe API client, Recharts for dashboards                                                         |
| Container orch | docker-compose      | One command brings the whole stack up                                                                 |

### Alternatives considered

- **FastAPI vs Django/Flask.** Django's ORM is heavier than I need; Flask
  doesn't give me Pydantic or async out of the box. FastAPI is the middle
  ground.
- **PostgreSQL vs MongoDB.** Expenses are relational — a user has many
  expenses, each has line items, budgets are per-category per-user. Mongo
  would force app-level joins or denormalisation. Not worth it.
- **Celery vs RQ vs arq.** Celery is heavier but has mature retry/DLQ and
  scheduling. For a project that will run production-style workloads it pays
  off.
- **MinIO vs local filesystem.** MinIO mirrors S3 so deploying to AWS/DO is a
  credentials swap, not a rewrite.

## Non-goals / deliberate simplifications

- **Single-tenant per deployment.** No orgs, no sharing. Owner == user.
- **No real-time push.** Client polls receipt status. Websockets are a
  feature, not a dependency.
- **Rule-based fallbacks everywhere.** OCR fails → GPT-4V. LLM fails →
  rules. No AI key configured → rules only. Never unusable.

## Roadmap

Tracked in `docs/roadmap.md` once milestones are broken down. First milestone
is a fully-wired local stack passing a health check — everything else waits
on that.

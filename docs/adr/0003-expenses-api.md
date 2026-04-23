# ADR-0003: Expenses CRUD API shape

- **Status**: Accepted
- **Date**: 2026-04-23
- **Deciders**: backend

## Context

Phase 3 ships the first read/write resource beyond auth. Every
subsequent feature (receipts → parsed expense rows, budgets → rolled-up
expense totals, insights → aggregates) hangs off this endpoint, so the
shape we pick here becomes load-bearing.

Requirements going in:

1. **Multi-tenant.** Every SpendLens user sees only their own rows.
   The product is single-tenant-per-user today but the API has to hold
   up the moment we ever share an instance.
2. **List is the hot path.** The dashboard is "a table of expenses";
   everything else is a side-quest. Queries need to page deep without
   degrading.
3. **Concurrent edits are a real thing.** Mobile + web + auto-ingest
   will write to the same row. We need a story for "the record
   changed under you".
4. **Filters that cover the dashboard from day one.** Category, date
   range, merchant search, amount range — all trivially combinable.
5. **No over-engineering.** This is Phase 3 of 8; we can't afford to
   bolt on every future requirement now.

## Decision

Ship a thin REST surface at `/api/v1/expenses` with five routes,
keyset pagination, weak ETags, and strict ownership enforcement at the
query layer.

### Routes

| Method  | Path               | Response codes                |
| ------- | ------------------ | ----------------------------- |
| `POST`  | `/expenses`        | `201`, `401`, `422`           |
| `GET`   | `/expenses`        | `200`, `401`, `400`, `422`    |
| `GET`   | `/expenses/{id}`   | `200`, `401`, `404`           |
| `PATCH` | `/expenses/{id}`   | `200`, `401`, `404`, `412`, `400` |
| `DELETE`| `/expenses/{id}`   | `204`, `401`, `404`, `412`    |

### Ownership: 404, not 403

Ownership is enforced in every query via a `user_id` clause — not by
loading the row and comparing IDs in Python. If a caller asks for an
expense they don't own, the service raises `ExpenseNotFoundError` and
the router returns `404 Not Found`. We **deliberately do not**
distinguish "exists but not yours" from "does not exist" — `403`
would let a caller enumerate IDs to probe existence.

Trade-off: a client can't tell the difference between "I deleted it"
and "it never existed". In practice that's the right default for this
class of product; the UX never needs to distinguish.

### Keyset pagination over offset

`LIMIT/OFFSET` pagination is O(offset) — page 1000 scans 1000 rows
before returning. With a "year of receipts" list that gets slow fast.

We ship cursor-based keyset pagination instead:

- Sort key: `(expense_date DESC, id DESC)` — `id` as the tiebreaker so
  same-day rows page deterministically.
- Cursor is opaque to the client (base64url JSON of the sort key).
  Shape is an implementation detail; the client round-trips it.
- `page_size` is bounded `1..100` at the FastAPI `Query` layer; out of
  range returns `422`. No silent clamp so hostile / buggy clients
  learn quickly.
- To detect "is there a next page?" we `LIMIT page_size + 1` and
  truncate — no second `COUNT` query.

Consequence: we can't jump to "page 47". We don't think we'll want
that UX for expense lists, and if we ever do, keyset still lets us
build numbered pagination on top (SQL `OVER()` windows, or a
background COUNT estimator).

### Weak ETags + `If-Match`

Every single-resource response carries `ETag: W/"<hash>"` where the
hash is `sha256(id:updated_at)` truncated to 32 chars.

- **Weak** (`W/` prefix) because the canonical form is JSON with no
  byte-for-byte guarantee (field ordering, whitespace).
- Mutating routes (`PATCH`, `DELETE`) accept an optional `If-Match`
  header. When present and stale we return `412 Precondition Failed`;
  when absent we skip the gate entirely. That keeps curl / scripts
  trivial to write while letting the SPA and mobile clients opt into
  optimistic concurrency.
- The ETag rotates on every successful write because `clock_timestamp()`
  gives per-statement wall-clock time (see Follow-ups on the migration
  from `now()` — it was wrong).

Why not always require `If-Match`? Two reasons:

1. First-party clients (the SPA we haven't built yet) can adopt it
   gradually. Gating behind a mandatory header before the UI is built
   is premature.
2. Background ingestion jobs in Phase 5+ write on behalf of the user
   without human review; there's no meaningful "stale" state to
   protect against.

### Filters, decided once

The `GET /expenses` query string supports:

- `category` — enum value
- `merchant` — case-insensitive substring (`ILIKE`)
- `date_from`, `date_to` — ISO-8601 dates, inclusive
- `min_amount`, `max_amount` — Decimal with `Numeric(12,2)` bounds
- `cursor`, `page_size`

All filters AND together. If merchant search becomes a hot path we
move to `pg_trgm` + a GIN index; not paying for that complexity now.

## Consequences

### Positive

- **Tenant isolation is enforced at the data layer.** A bug in a
  future route that forgets to filter by `user_id` would fail closed
  (empty result) rather than leak. The service signature
  (`user_id=` keyword-only) makes it nearly impossible to call without.
- **List scales.** Keyset paging means page 10 000 costs the same as
  page 1. The existing composite index
  `ix_expenses_user_date (user_id, expense_date)` covers the sort.
- **Concurrent edits have a standard-track answer.** 412 is exactly
  what HTTP gives us for stale-write detection; no bespoke `version`
  column.
- **Small, reviewable PRs.** Phase 3 shipped as 4 PRs (#9, #10, #11,
  this one) — schemas, service, routes, docs — each green in CI.

### Negative

- **No numbered pages.** If the product ever wants "Page 47 of 320"
  for expenses we build estimated counts on top. Acceptable trade.
- **`If-Match` optional** means race windows exist for clients that
  skip it. That's a feature, not a bug — we don't want to turn every
  CLI into a two-request dance — but it has to be documented.
- **404 on cross-tenant access** is subtly harder to debug in support
  tickets ("why doesn't my ID work?" could mean five different
  things). Logged consistently server-side under `expenses.not_found`.

### Follow-ups

- **Rate limiting on `POST /expenses`.** A malicious client could
  create 10k rows. Not pressing for single-user deployments, becomes
  real in the Phase 8 public launch.
- **Bulk endpoints.** Phase 5 OCR will create expenses in batches;
  we'll add `POST /expenses:batch` when it's needed, not now.
- **`pg_trgm` index on `merchant_name`.** Defer until we see list
  endpoints hot in production.
- **Soft delete vs hard delete.** Currently hard-deletes via ORM
  cascade. Budgets aggregate live; once insights add historical
  reporting we may want a `deleted_at` column so totals don't shift
  retroactively.
- **Refund / credit modelling.** Right now amounts are strictly
  positive. A later ADR will cover how refunds pair with their
  originating expense (separate row linked via FK, not a negative
  amount; negatives silently break category totals).

## Drive-by fixes that happened alongside

Two latent Phase 1 model bugs surfaced while writing the service
tests; we fixed both in PR #10 rather than ship around them:

1. **Enum label mismatch.** SQLAlchemy default is to send the Python
   enum `.name` (`FOOD_DINING`), but the migration created Postgres
   enums with lowercase labels (`food_dining`). Every insert touching
   an enum column would have failed at runtime. New `pg_enum()` helper
   wires `values_callable=lambda e: [m.value for m in e]`. No
   migration needed — DB was already right.
2. **`TimestampMixin` used `now()`.** `now()` returns the transaction
   start time in Postgres, so two updates in one tx got identical
   `updated_at`. That defeats ETag rotation entirely. Swapped to
   `clock_timestamp()` — per-statement wall-clock — which is what
   `updated_at` should have been in the first place.

## Alternatives considered

### Offset/limit pagination

Rejected — the mobile dashboard will list "this month's expenses"
which could be hundreds of rows on a heavy user. Keyset wins at
negligible implementation cost.

### Strict `If-Match` on every mutation

Rejected. Would force CLI/curl users into a two-request pattern
(GET-then-PATCH) and make background ingestion jobs in Phase 5+
awkward. Optional by default, easy to tighten later if needed.

### 403 on cross-tenant access

Rejected. Leaks existence of IDs the caller doesn't own — a trivial
enumeration oracle. `404` costs us a tiny bit of debuggability and
removes a full class of info-disclosure bug.

### Version column instead of ETag

Rejected for now. Integer row-versioning is a valid alternative but
requires an extra column and ORM `version_id_col` wiring; ETag over
`updated_at` is idiomatic HTTP and takes one helper function.

### Separate `GET /expenses/search` route

Rejected. Filters live on the main list route as query params. Adding
a parallel search route just doubles the surface area with no extra
behaviour.

## References

- RFC 9110 §13.1.1 (Precondition headers, `If-Match`).
- RFC 9110 §8.8.3 (`ETag` header).
- Markus Winand, *Use the Index, Luke* — keyset pagination:
  https://use-the-index-luke.com/no-offset
- `app/api/v1/endpoints/expenses.py`, `app/services/expense_service.py`,
  `app/core/pagination.py`.

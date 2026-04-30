# ADR-0007: Insights & analytics architecture

- **Status**: Accepted
- **Date**: 2026-04-28
- **Deciders**: backend, frontend

## Context

Phase 6 turns the receipt-and-expense store into an analytics surface
the user actually opens for spending decisions. Six questions had to
land together:

1. **Where does the analytics live?** Do we add a separate
   warehouse / column store, or aggregate against the OLTP table?
2. **Push or pull?** Pre-compute monthly rollups into a side table,
   or query on demand?
3. **What's "anomalous"?** A z-score against a rolling baseline is
   the textbook answer; the questions are which baseline window,
   which population stddev, and how to filter out the meaningless
   cases.
4. **Budget semantics.** Per-category-per-period? What happens
   when a user pauses one? How does "alert at 80%" surface in the
   UI without becoming noise?
5. **Frontend chart library.** Recharts vs Chart.js vs roll-our-own
   SVG?
6. **Wire shape for charts.** Sparse rows or pre-pivoted dense grid?

We're working inside the same Phase 0 constraints: self-hosted-first,
Postgres-only, single dev, deferred complexity.

## Decision

**Aggregate against the OLTP `expenses` table on demand.** No
analytics warehouse, no materialised views, no rollup tables. Three
endpoint families (monthly breakdown, trends, anomalies) plus the
budget CRUD + status surface — each one a single round trip,
ownership-scoped at the WHERE clause. Frontend dashboards via
**Recharts**, fed a **dense (month × category) grid** the backend
pre-pivots so chart code never reasons about gaps.

Every choice in detail:

### Aggregate on demand, no warehouse

We considered a side table (`monthly_rollups`) maintained by the
OCR pipeline alongside Phase 5's categorisation, plus a Phase 6+
sweeper to backfill. Rejected. Three reasons:

1. **Volume.** Personal finance scales as ~50–500 expenses per user
   per month. Postgres aggregations over a year of that data
   ("SUM and GROUP BY for 12 user × N category"), with the existing
   `ix_expenses_user_date` index, run in milliseconds. The
   warehouse's job here is "make a fast query faster" — not worth
   the consistency story.
2. **Drift.** Every rollup table needs an invalidation story for
   `PATCH /expenses` and `DELETE /expenses`. We already saw the
   pain when adding the corrections feedback loop — a second cache
   to sync would compound it.
3. **One round trip per chart.** The dashboard's hot path is "open
   `/insights` and see four cards"; four queries against indexed
   data hit Postgres for ~tens of ms total.

If a user ever sits on tens of thousands of rows we'll add a
materialised view; we won't pre-pay that complexity now.

### Half-open month bounds, shared helper

`month_bounds(date) → (first_of_month, first_of_next_month)`. Every
aggregate uses it. The half-open interval avoids the
inclusive-vs-exclusive footgun that creeps in when callers do their
own arithmetic — a March-31 expense in an April query is a real
production bug, and pinning the helper closes the off-by-one.

### Trends: dense grid, server-side

The trends endpoint returns a *dense* (month × category) grid with
zero buckets explicit. Sparse SQL → dense client render is a
recurrent gap-handling bug in chart libraries; we pivot in Python
once and ship a shape Recharts can index without checks.

`MAX_TRENDS_MONTHS = 60` keeps a hostile / buggy client from
running unbounded sweeps.

### Anomalies: z-score with three statistical defenses

Recent expenses scored against a rolling per-category baseline:

* **Baseline window**: `[today - 180d, today - lookback)`. Per-
  category mean + sample stddev computed in a Postgres CTE.
* **Lookback window**: default 30 days. Each expense is z-scored
  against its category's baseline; rows ≥ `z_threshold` (default
  2.0) surface, ordered desc, capped at 50.

Three defenses against meaningless flags:

1. **Sample-size floor** (`MIN_BASELINE_SAMPLES = 5`). Categories
   with fewer baseline rows skip — stddev on three observations
   is noise, not signal.
2. **Zero-stddev filter.** Identical-amount subscriptions have
   stddev = 0; the CTE `HAVING` clause filters them so the join
   can't divide by zero.
3. **Sample stddev (`stddev_samp`)**, not population — the
   inferentially-correct choice. Population stddev would
   understate variance and produce too many false positives.

Wire shape echoes the analysis window back (`baseline_start`,
`lookback_start`, `z_threshold`) so the UI can render "we looked at
the last 30 days against the previous 6 months" without
re-deriving the date math.

### Budgets: monthly-only, soft-delete via `active`

The `budgets` table from Phase 1 already had every column we
needed. The CRUD surface follows the expenses pattern (404 on
cross-user, ETag-shaped contract minus the optimistic-concurrency
gate, unique constraint on `(user_id, category, period)` enforced
at DB layer → 409 on duplicate POSTs).

Two semantic choices worth pinning:

* **`active=false` is soft-delete.** `DELETE` is for "not tracking
  this category anymore"; flipping `active` is for "paused without
  losing the threshold history".
* **Status query skips non-monthly periods.** `BudgetPeriod` only
  has `MONTHLY` today; the moment `WEEKLY` / `YEARLY` ship we'll
  route each through its own period-bounds helper. A weekly
  budget on a monthly window would silently misreport progress, so
  the period filter is explicit.

### Status `ratio` clamps at zero, *not* at one

The status response returns `ratio = spent / amount` clamped at 0
on the low end (no negative spend) but allowed > 1 on the high end.
A user 50% over budget should see `ratio = 1.5`, not `1.0` — the
front-end renders the bar fill at 100% but the percentage label
keeps climbing so "150%" reads as red.

### Chart library: Recharts

Three options were on the table. Recharts won because:

* **Native React.** ``<BarChart>`` / ``<Pie>`` are React components,
  not imperative canvas calls — composition with hooks is
  straightforward.
* **SVG output.** Renders in jsdom for tests; degrades cleanly
  without JS for screen readers.
* **Tree-shakable.** Only the chart types we use ship in the
  bundle.

Bundle cost: +114 KB gzipped, total 202 KB. Code-split via
`React.lazy` is a follow-up if it bites.

Rejected: Chart.js (canvas, harder to test, react-chartjs-2 wrapper
is a third-party shim); D3 (too low-level for the four charts we
need); Visx (Airbnb's project, less momentum than Recharts).

### Decimal-as-string discipline

API surface keeps decimals as strings (same convention as
expenses). Conversion to `Number` happens only inside Recharts data
accessors and tooltip formatters, never before display. JS floats
eating cent precision is a perennial source of "the totals don't
add up" bugs in finance UIs.

### Empty states framed positively

The anomalies card's empty state reads "Nothing unusual this
month" rather than "no data". The most common reason no rows render
is that the user is genuinely on track — alarming copy on the
healthy state is the wrong framing.

## Consequences

### Positive

- **Single Postgres, single source of truth.** Every insight ties
  back to the same `expenses` rows. PATCH / DELETE / new
  categorisations show up in the next `/insights` GET without a
  cache-warm step.
- **Statistical defenses are explicit and testable.** Sample-size
  floor, zero-stddev filter, and sample stddev each have unit
  tests that seed the failure case.
- **Wire shape is chart-ready.** The dense (month × category) grid
  removes the entire class of gap-handling bugs from the front-
  end.
- **Self-hosted users without an OpenAI key still get every
  insight.** The pipeline LLM is optional; the analytics is pure
  SQL.
- **Same ownership-in-WHERE rule across every endpoint.** A router
  bug that drops `user_id` fails closed (empty result / 404)
  rather than leaking another tenant's spend.

### Negative

- **No analytics warehouse means no historical multi-user
  reporting.** Fine for a personal-finance product; would need
  rethinking for any "compare yourself to peers" feature.
- **Recharts is heavy.** +114 KB gzipped is a real cost on slow
  mobile. Code-splitting deferred.
- **Anomaly thresholds are global, not per-user.** A frugal user
  and a high-spender share the same z-score floor. Per-user
  threshold learning is a Phase 8+ idea.
- **Budget periods are monthly-only.** Weekly / yearly support
  would touch the status query, the schema enum, and the front-
  end period selector — deferred until a real user asks.
- **Anomaly endpoint queries the full lookback window every
  call.** Cache headers + TanStack Query already short-circuit
  most refetches; if a power user sees actual cost, we add a
  short-TTL `Cache-Control` and re-evaluate.

### Follow-ups

- **Budget periods beyond monthly** (weekly, yearly) when a real
  user requests them. The schema already carries `BudgetPeriod`;
  the work is the `status` query plus a UI period selector.
- **Per-user anomaly threshold learning.** Track each user's
  click-through on flagged anomalies; tune `z_threshold` toward
  what they engage with.
- **Materialised monthly rollups** if any user crosses the
  ten-thousand-rows-per-month line. Refreshed by a Phase 5
  pipeline trigger after categorisation; the on-demand query
  becomes the fallback.
- **CSV / spreadsheet export** of insights for tax season.
- **Per-category sub-trends.** "Show me Food & Dining over time"
  by zooming the trend chart on a single category.
- **Code-split Recharts** via `React.lazy` if the bundle cost
  bites.

## Alternatives considered

### Materialised views / rollup tables up front

Rejected. Premature optimisation at our scale — and would require
an invalidation story for every PATCH / DELETE / categorise round
trip. We add it when on-demand SQL gets slow, not before.

### Anomaly detection via the LLM

Rejected. Way more expensive per call, non-deterministic, and the
classical statistical approach catches the cases users actually
care about (a $200 latte) without any model risk. The LLM stays
restricted to categorisation, where its language understanding is
the right tool.

### Median + IQR instead of mean + stddev

Considered. IQR is more robust to skewed distributions. We picked
mean + stddev because: (a) Postgres has `stddev_samp` natively,
(b) the explanation is shorter ("X is 2× your usual spend" reads
better than "X is outside the interquartile range"), and (c) the
defensive floors (sample size + zero-stddev filter) handle the
distributions where IQR would be the better choice anyway. We can
swap the metric in one place later if real users surface false
positives.

### Pre-fetching all four cards in one endpoint

Rejected. Four small focused queries are easier to cache, paginate,
and debug than one mega-payload. TanStack Query handles the four
parallel loads transparently on the front-end.

### Chart.js + react-chartjs-2

Rejected. Canvas rendering means harder testing in jsdom and worse
a11y. Recharts' SVG approach hits both targets without a wrapper
library.

### Sparse trend buckets

Rejected. Front-ends consistently get gap-handling wrong on sparse
chart data. Pivoting on the server costs nothing and removes an
entire class of bug.

## References

- Sample stddev vs population stddev: <https://en.wikipedia.org/wiki/Bessel%27s_correction>
- Recharts docs: <https://recharts.org/>
- W3C `progressbar` ARIA pattern: <https://www.w3.org/WAI/ARIA/apg/patterns/meter/>
- `backend/app/services/insights_service.py`,
  `backend/app/services/budget_service.py`,
  `backend/app/api/v1/endpoints/insights.py`,
  `backend/app/api/v1/endpoints/budgets.py`,
  `frontend/src/api/insights.ts`,
  `frontend/src/pages/InsightsPage.tsx`.

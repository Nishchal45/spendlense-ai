# ADR-0005: OCR + categorisation pipeline architecture

- **Status**: Accepted
- **Date**: 2026-04-27
- **Deciders**: backend

## Context

Phase 5 turns a stored blob (Phase 4) into a categorised expense the
user actually sees in their dashboard. Five questions had to land
together:

1. **Sync vs. async.** OCR can take seconds; the LLM call can take
   longer. We can't block the upload request on either.
2. **One pipeline or two.** OCR and categorisation are *different*
   workloads — Tesseract is CPU-bound and deterministic, the LLM is
   network-bound and probabilistic. Failure modes differ. Retry
   semantics differ.
3. **What does the categoriser actually use.** Rules-only is fast
   but stupid; LLM-only is smart but breaks for self-hosted users
   without an API key, costs money on every receipt, and never
   learns from user fixes. The product needs all three.
4. **What happens when OCR is wrong.** Tesseract chokes on faded
   thermal paper, off-angle photos, and handwriting. The pipeline
   needs an escape hatch that's better than "your receipt is
   broken, sorry".
5. **State and recovery.** Receipts move through a state machine
   that clients poll. Failed rows need a recovery path that doesn't
   require operator intervention.

We're working inside these constraints:

- **Self-hosted-first.** The whole pipeline has to work on a laptop
  with `docker compose up` and *no* OpenAI key. AI features are an
  upgrade, not a hard dependency.
- **Stack continuity.** We already run Redis (caching) and Postgres.
  Adding RabbitMQ or a separate task DB just for Phase 5 is cost we
  don't want to pay until volume justifies it.
- **Cheap retries.** Cloud LLM calls hit rate limits; Tesseract
  occasionally segfaults on a malformed JPEG. The pipeline has to
  shrug those off without operator intervention.

## Decision

Run the work on **Celery with a Redis broker**. Split into two
Celery tasks (OCR and categorisation) chained on the
parsed→categorised state transition. Categorise via a three-layer
priority chain (corrections → rules → LLM). When Tesseract
confidence is low, fall back to **GPT-4V** for structured
extraction. Expose **status polling** and **retry** endpoints for
client recovery. Rasterise PDFs via **poppler-utils + pdf2image** so
email receipts travel the same path as phone photos.

### State machine

```
       upload          worker picks up        OCR succeeds        Expense created
uploaded ──────▶ uploaded ──────▶ processing ──────▶ parsed ──────▶ categorised
                                       │                │
                                       │ OCR fails      │ categorise fails
                                       ▼                ▼
                                    failed           failed
                                       ▲
                                       │ POST /retry
                                  (resets to uploaded)
```

Three properties of this design that are non-obvious:

- **`processing` is observable.** The OCR worker writes
  ``status=processing`` *before* it starts the heavy work, so a
  client polling ``GET /receipts/{id}/status`` sees motion within a
  second of upload — not a frozen ``uploaded`` for the full OCR
  duration.
- **`failed` is terminal-without-data-loss.** A failed row keeps
  its blob, its mime type, its byte count. ``POST /retry`` resets
  ``status``/``error_message`` and re-enqueues — no re-upload, no
  duplicate object in S3.
- **Idempotent across the chain.** Both tasks tolerate being run
  twice on the same row. The categorise task explicitly checks for
  an existing expense (the unique constraint on
  ``expenses.receipt_id`` is the floor).

### Why Celery + Redis broker

- **One fewer moving part.** Redis is already in the stack for
  caching. Riding on it saves a RabbitMQ container in dev / CI /
  prod for what is, today, a low-throughput pipeline. If volume
  justifies it later we move the broker — that's a config change,
  not a code change.
- **Battle-tested.** Celery's retry semantics, time limits, and
  acks-late behaviour are exactly what we'd hand-roll if we did
  this from scratch.
- **Visibility.** Per-task timing and failure events emit naturally;
  the eventual metrics backend (Phase 8) plugs in without code
  changes.

Wire choices that matter:

- **JSON-only serialization.** Pickle in a broker is RCE-by-design
  if the broker ever lands on a shared host.
- **`task_acks_late=True` + `prefetch_multiplier=1`.** A worker
  crash mid-task hands the job to another worker rather than
  silently dropping it.
- **Soft 120 s / hard 180 s time limits.** A stuck Tesseract job
  can't pin a worker forever.
- **Explicit ``conf.imports``.** Task discovery is a list in source,
  not autodiscover guesswork. Easy to read, easy to extend.

### Two tasks, not one

`process_receipt` (OCR) and `categorise_receipt` (LLM + rule lookup
+ Expense row creation) are separate Celery tasks chained on the
state transition. Three reasons:

1. **Different failure shapes.** Tesseract not reading bytes is
   permanent — retrying does nothing useful. An OpenAI 429 is
   transient — exponential backoff is exactly right. One task with
   one retry policy can't be both.
2. **Reprocess corrections cheaply.** When a user corrects a
   category we may want to re-run categorisation across their
   historical receipts (without re-OCRing). A standalone categorise
   task makes that a one-line dispatch.
3. **Failure isolation.** A buggy LLM prompt change can't take down
   the OCR worker.

### Categorisation: corrections → rules → LLM → OTHER

Priority chain, highest to lowest:

1. **User corrections** (`category_corrections` table). If the user
   ever corrected an expense for this merchant, that wins forever.
   This is the *whole point* of the feedback loop: user fixes are
   sticky. Atomic upsert via Postgres `ON CONFLICT (user_id,
   merchant_name)` so two devices correcting the same merchant
   simultaneously don't race.
2. **Static rule map.** ~50 hand-curated substring patterns
   (Starbucks → FOOD_DINING, Costco → GROCERIES, etc.). Offline,
   deterministic, free. Order matters: `"uber eats"` checked
   before `"uber"` so food-delivery doesn't get misfiled as
   ride-share.
3. **LLM (`gpt-4o-mini`).** Locked classification prompt
   (`temperature=0`), JSON-mode response, max 16 output tokens.
   Validates against the `ExpenseCategory` enum — anything else is
   treated as "no opinion".
4. **`OTHER`.** Floor.

The chain has *three* layers because each one breaks differently:
rules don't know about merchants we didn't list, LLM doesn't know
which Costco *this user* treats as groceries vs. shopping,
corrections don't know anything until the user edits something. Each
layer fixes the previous layer's failure mode.

**Self-hosted users without an OpenAI key** still get rules +
corrections — only the third layer is gated behind the key. The
floor is `OTHER`, not "your receipt is unprocessable".

### Tesseract → GPT-4V fallback

When Tesseract's mean per-word confidence is below
`settings.ocr_confidence_threshold` (default `60.0` on the 0–100
scale) *and* an OpenAI key is configured, the same image goes to
`gpt-4o` for structured extraction. The vision model returns
merchant / total / date directly — there's no "OCR text → regex
parser" round trip — which is the right shape for the kinds of
images Tesseract chokes on:

- Faded thermal-paper receipts
- Phone photos at an angle / in poor lighting
- Handwritten receipts (taxis, small businesses)
- Layouts Tesseract can't segment (multi-column, watermarks)

`ocr_method` (`tesseract` vs `gpt4v`) is recorded on the row so
analytics can see how often the fallback fires — too often means
Tesseract config needs tuning, too rarely means the threshold is
too low.

**Vision never raises into the pipeline.** No key, network error,
or malformed JSON all return `None` and the caller keeps the
Tesseract reading. Vision is an upgrade path, never a hard
dependency.

### Status polling, not push

`GET /receipts/{id}/status` returns the freshest pipeline state plus
`error_message` and `parsed_payload`. Clients poll this on an
interval until the row reaches `categorised` or `failed`.

Push notifications (WebSocket / SSE) would be lower-latency but
require connection state in the API process and a fan-out story for
multi-instance deploy. Not worth it for a pipeline that completes in
seconds. Phase 8's mobile app gets push via APNs/FCM, which is the
right channel for that surface anyway.

### `POST /retry` resets and re-enqueues

A failed row is recoverable: `POST /receipts/{id}/retry` flips the
status back to `uploaded`, clears `error_message`, and re-enqueues
the OCR task. Returns 202 Accepted (not 200) because the actual
reprocessing happens on the worker.

`409 Conflict` if the row isn't currently in `failed` — retrying a
row that's still in flight, or one that already produced an
expense, is a logic bug on the client side and we surface it rather
than silently re-run the pipeline.

### PDF rasterisation: first page, 200 DPI

Email receipts (Uber, Amazon, airline) routinely arrive as PDF.
`pdf2image` shells out to `pdftoppm` from the Debian
`poppler-utils` package — installed in the Dockerfile and CI
workflow — to rasterise the first page at 200 DPI, then send the
resulting image down the standard Tesseract path.

DPI was the trade-off: 150 DPI lost small print on a typical Uber
Eats PDF; 300 DPI was 2× slower with no readability gain. 200 is
the sweet spot in spot-checks.

**First page only.** Multi-page hotel folios and B2B invoices are
the long tail; we'll add page-selection heuristics when a real user
complains. Going multi-page now would mean either OCRing every page
(cost) or guessing which page has the totals (complexity).

Garbage PDF bytes raise `PdfRasterError`, which the task surfaces
as a domain failure (no retry — bad PDFs don't get better).

### Per-task DB engine

Celery workers are sync; our DB code is async. Each task wraps its
work in `asyncio.run`, which opens a fresh event loop. asyncpg
connection objects are loop-bound — reusing the module-global engine
across separate `asyncio.run` calls strands connections on closed
loops with `Event loop is closed`.

Fix: each task builds its own engine inside its loop and disposes
it before the loop closes. Milliseconds of overhead per task,
correctness guaranteed. Production tasks run for seconds or longer
— engine setup cost is negligible.

## Consequences

### Positive

- **The pipeline gets smarter the more it's used.** Every category
  correction is one less LLM call (and one more correct guess) for
  that user-merchant pair forever.
- **Self-hosted users get a working product without a key.** Rules
  + corrections cover most cases; OTHER is the floor.
- **Real escape hatch for hard receipts.** Vision fallback turns
  "Tesseract can't read this" from a `failed` row into a
  successfully extracted one — the canonical reason a portfolio
  product looks polished or doesn't.
- **Failure recovery is one click for the user.** No support ticket
  for transient OCR failures.
- **Pipeline is observable end-to-end.** Status state machine,
  Celery per-task events, structured log lines for every transition.
- **Backend-agnostic broker.** Redis today, RabbitMQ later if
  volume demands. Code doesn't change.

### Negative

- **The categoriser is opinionated.** A pre-curated rule map
  reflects English-speaking, US-centric merchant patterns. International
  users will see more `OTHER` until either rules expand or the LLM
  fills the gap. The corrections loop is the long-term fix.
- **Vision adds API cost on hard receipts.** Every fallback is a
  paid call. Mitigated by the threshold gate (only fires below
  60% confidence) and by the per-user correction loop (once a user
  corrects, future receipts from that merchant skip the LLM
  entirely).
- **Two tasks means two failure surfaces.** A bug in either task
  can leave a row stranded. The retry endpoint handles failed rows;
  rows stuck in `processing` (e.g. a worker crashed mid-task) need
  a sweeper, deferred to Phase 6.
- **PDFs are first-page only.** Multi-page receipts won't fully
  parse until we add page selection.
- **Task-local DB engine is a per-task cost.** ~5 ms of engine
  setup per invocation. Negligible at our throughput; revisit if a
  per-second job rate makes this matter.

### Follow-ups

- **Sweeper for stuck rows.** Nightly cron that finds rows in
  `processing` for > N minutes and either re-enqueues or marks
  failed. Phase 6.
- **PDF page selection.** Heuristic: if the first page has no
  amounts on it, try the last page. Add when a user complains.
- **Vision cost ceiling.** Per-user / per-month cap on fallback
  calls so a hostile client can't run up an LLM bill. Defer to
  Phase 8 alongside other public-launch hardening.
- **Webhook for expense-created.** Push channel for integration
  with downstream systems (budgets, anomaly detection in Phase 6,
  webhooks for Zapier-style automations).
- **Multi-language receipts.** Tesseract supports `lang=eng+spa`
  etc.; we ship `eng` only. Add when a real user sends a
  non-English receipt.

## Alternatives considered

### One Celery task, not two

Rejected. OCR and LLM have fundamentally different failure shapes
(deterministic vs. probabilistic, CPU-bound vs. network-bound, no
external API vs. paid API). Forcing them into one task means one
retry policy, which is wrong for at least one of them.

### LLM-only categorisation

Rejected. Costs money on every receipt; breaks for self-hosted
users without a key; never learns from user fixes (every receipt is
a fresh classification call regardless of past corrections).

### Rule-only categorisation

Rejected. Doesn't scale beyond a curated list. Misses every
merchant we didn't pre-load.

### Push notifications instead of polling

Rejected for now. A pipeline that completes in seconds doesn't
benefit enough from push to justify the connection-state + fan-out
complexity. Mobile app in Phase 8 will get APNs/FCM — the right
channel for cross-device reach.

### Synchronous OCR in the upload request

Rejected outright. Tesseract on a phone photo can take 1–3 s; an
LLM round-trip can take 5+. Blocking the upload request for that
duration means timeouts on flaky mobile networks and idle API
workers tied up on the wire.

### Storing OCR state in Redis instead of Postgres

Rejected. The `receipts` row is the source of truth — it carries
the blob key, the parsed payload, the foreign key to `expenses`.
Splitting state across Redis and Postgres just to "make polling
faster" creates two consistency stories where one is sufficient.

### `pytesseract` only, no GPT-4V

Rejected. Tesseract has known weak spots (faded thermal paper,
handwriting, weird angles) that an LLM with vision handles trivially.
Without a fallback, those receipts land permanently in `failed` and
the user has to type them in by hand — defeating the product.

### `python-magic` / `libmagic`

Rejected for MIME sniffing in Phase 4 (see ADR-0004). Not relevant
here, but reaffirms: we don't add native deps for things we can do
with a tiny lookup table.

### Multi-page PDF support up front

Rejected. The long tail (hotel folios, B2B invoices) doesn't justify
the page-selection complexity. First page covers the vast majority;
add heuristics when a real user complains.

## References

- Celery best practices: <https://docs.celeryq.dev/en/stable/userguide/tasks.html>
- OpenAI vision API: <https://platform.openai.com/docs/guides/vision>
- Tesseract LSTM accuracy notes: <https://tesseract-ocr.github.io/tessdoc/Performance.html>
- `backend/app/tasks/process_receipt.py`,
  `backend/app/tasks/categorise_receipt.py`,
  `backend/app/services/categorisation.py`,
  `backend/app/services/vision_ocr.py`,
  `backend/app/services/pdf_rasterise.py`,
  `backend/app/api/v1/endpoints/receipts.py`.

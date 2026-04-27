"""Celery application factory for the background pipeline.

The OCR + categorisation pipeline runs on Celery because the work is
genuinely async-from-the-API: a phone uploads a receipt, the user
walks away, the worker takes seconds-to-minutes to OCR + categorise.
A queue gives us:

* Retries with exponential backoff for transient OCR/LLM failures
  without blocking the upload request.
* Horizontal scale: multiple worker containers consume the same queue
  in production without code changes.
* Observability — Celery emits per-task timings/failure events that
  ship straight to whatever metrics backend Phase 8 lands.

Broker: Redis. We already run Redis for caching/session work, so
piggybacking on it avoids a second piece of infrastructure for what
is, today, a low-throughput pipeline. If volume justifies it later
we move to RabbitMQ — that's a config change, not a code change.

Result backend: also Redis. Most tasks update Postgres rows directly
(``receipts.status`` etc.), so the result backend is mostly for
``AsyncResult.get()`` in tests. ``result_expires`` keeps the
keyspace bounded.

Task discovery: explicit. ``conf.imports`` lists every task module so
the worker registers them at boot without autodiscover guesswork —
and tests that need a task can import the function directly without
spinning up a worker.

Eager mode: tests flip ``task_always_eager`` so the task body runs
in-process under pytest. Production never touches that flag — the
override lives in ``conftest.py``.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

# Explicit task-module list. Add new modules here when they're
# introduced; the worker imports them at startup, registering every
# ``@celery_app.task`` decorator they contain.
_TASK_MODULES: tuple[str, ...] = ("app.tasks.ping",)


def _build_app() -> Celery:
    settings = get_settings()
    redis_url = str(settings.redis_url)

    app = Celery("spendlens", broker=redis_url, backend=redis_url)

    app.conf.update(
        # JSON only — pickle is a remote-code-execution footgun if a
        # broker ever lands on a shared host. Every task argument is a
        # primitive (UUID strings, ints, dicts).
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        # UTC across the board so logs/correlation timestamps line up
        # with the ``TimestampMixin`` rows the worker writes.
        timezone="UTC",
        enable_utc=True,
        # Don't let one slow Tesseract job sit on a worker forever.
        # Soft = clean shutdown via SoftTimeLimitExceeded, hard = SIGKILL.
        task_soft_time_limit=120,
        task_time_limit=180,
        # Acknowledge tasks *after* they finish, not on receipt — so a
        # worker crash mid-task hands the job to another worker rather
        # than silently dropping it.
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        # Trim the result keyspace; nothing reads results past an hour.
        result_expires=3600,
        imports=_TASK_MODULES,
        # Celery 6.0 splits broker-retry behaviour between *startup*
        # and *runtime*. Set the new flag explicitly so the worker
        # keeps retrying broker connects across a Redis bounce, and
        # to silence the pending-deprecation warning that fires
        # otherwise.
        broker_connection_retry_on_startup=True,
    )

    return app


celery_app = _build_app()

"""Worker liveness sanity task.

``ping`` is the simplest possible task that crosses the broker — used
by the dev setup to confirm a worker is consuming, and by the test
suite to verify Celery is wired correctly without dragging in OCR or
LLM dependencies. A real readiness probe lives in Phase 5+ once the
worker pool is on the deploy critical path.
"""

from __future__ import annotations

from app.tasks.celery_app import celery_app


@celery_app.task(name="spendlens.ping")
def ping() -> str:
    return "pong"

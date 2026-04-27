"""Background-task surface for the OCR + categorisation pipeline.

The Celery app instance lives in :mod:`app.tasks.celery_app`. Task
modules sit alongside it (``ping``, future ``process_receipt`` etc.)
and register themselves via the ``@celery_app.task`` decorator.

This package is intentionally light — no symbol re-exports — so
worker startup, test imports, and FastAPI's request-time enqueue path
all use the same explicit module path
(``from app.tasks.celery_app import celery_app``). Sidesteps an
import cycle where ``__init__`` would import the singleton and the
singleton would, in turn, import sibling task modules to register
them.
"""

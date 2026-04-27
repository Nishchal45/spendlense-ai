"""Smoke tests for the Celery wiring.

These tests don't talk to a real worker — they rely on the eager-mode
override set in ``conftest.py``. The point is to assert that:

1. Task discovery works: ``spendlens.ping`` shows up in the registry,
   so worker boot would find it.
2. Eager invocation runs the task body and returns a result.
3. Configured serializers and timezone are what we expect, so the
   "JSON only / UTC always" guarantee from the ADR holds.
"""

from __future__ import annotations

from app.tasks.celery_app import celery_app
from app.tasks.ping import ping


def test_ping_task_is_registered() -> None:
    # Worker startup discovers tasks via ``conf.imports``. If a future
    # refactor accidentally drops a module from that list, this fails
    # before the worker silently stops processing the task type.
    assert "spendlens.ping" in celery_app.tasks


def test_ping_returns_pong_via_delay() -> None:
    # ``.delay()`` is the production code path — confirms the eager
    # override is wired so test runs don't hang on a missing broker.
    result = ping.delay()
    assert result.get(timeout=5) == "pong"


def test_ping_returns_pong_when_called_directly() -> None:
    # Direct call bypasses Celery entirely; useful for code paths that
    # want to test task logic without the queue indirection.
    assert ping() == "pong"


def test_serializers_are_json_only() -> None:
    # Pickle is a remote-code-execution risk if a broker ever lands on
    # a shared host. Lock JSON-only at config time so a future
    # contributor can't quietly add ``pickle`` to ``accept_content``.
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_timezone_is_utc() -> None:
    assert celery_app.conf.timezone == "UTC"
    assert celery_app.conf.enable_utc is True

"""Celery task: walk Gmail history for one connection.

Triggered by the Pub/Sub push handler. Its job is *only* to bridge
sync→async + own a DB session — the actual sync logic lives in
:mod:`gmail_sync_service`. Same shape as ``process_receipt`` so the
loop / engine lifetime story is identical, and a future drift in
either task surfaces as a single review item.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.gmail_connection import GmailConnection
from app.services.gmail_sync_service import sync_connection
from app.tasks.celery_app import celery_app

log = structlog.get_logger()


@celery_app.task(name="spendlens.gmail_history_sync", bind=True, max_retries=3)
def gmail_history_sync(self: Any, connection_id: str, push_history_id: str) -> None:
    """Entry point Celery dispatches to. Delegates to the async body."""
    try:
        _run_sync(_run(connection_id, push_history_id))
    except Exception as exc:  # noqa: BLE001 — Celery's retry needs the bare exception
        # Same backoff curve as ``process_receipt`` (2/4/8 s). Three
        # retries cover transient Google-side flakes; permanent
        # failures (revoked token, deleted connection) raise out of
        # the worker after the third attempt and the row stays put
        # — operator-driven recovery.
        raise self.retry(exc=exc, countdown=2 ** (self.request.retries + 1)) from exc


def _run_sync(coro: Coroutine[Any, Any, None]) -> None:
    """Bridge sync Celery → async body, identical to ``process_receipt``."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(asyncio.run, coro).result()


@asynccontextmanager
async def _task_session() -> AsyncIterator[AsyncSession]:
    """Per-invocation DB session bound to this task's event loop.

    asyncpg connections are loop-bound; reusing the module-global
    engine across ``asyncio.run`` calls strands sockets on closed
    loops. Open + dispose per task — same trade-off documented in
    ``process_receipt._task_session``.
    """
    engine = create_async_engine(str(get_settings().database_url))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _run(connection_id_str: str, push_history_id: str) -> None:
    connection_id = UUID(connection_id_str)
    async with _task_session() as session:
        connection = (
            await session.execute(
                select(GmailConnection)
                # ``sync_connection`` reads ``connection.user.inbox_token`` to
                # synthesise the inbound-email ``to`` field. Eager-loading
                # the relationship saves a lazy-load round trip that would
                # otherwise need its own ``await``.
                .options(selectinload(GmailConnection.user))
                .where(GmailConnection.id == connection_id)
            )
        ).scalar_one_or_none()
        if connection is None:
            # Connection was deleted between push and pickup. Nothing
            # to sync; nothing to retry.
            log.warning(
                "gmail_history_sync.connection_missing",
                connection_id=connection_id_str,
            )
            return

        async with httpx.AsyncClient(timeout=15.0) as client:
            await sync_connection(
                session,
                connection=connection,
                push_history_id=push_history_id,
                client=client,
            )

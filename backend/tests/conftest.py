"""Shared pytest fixtures.

The strategy for DB-backed tests:

1. A session-scoped fixture (``_prepare_test_database``) drops and
   recreates the test database, then runs migrations against it. This
   gives the run a deterministic schema.
2. A per-test fixture (``db_session``) opens a connection, begins an
   outer transaction, and binds the ORM session to that connection.
   After the test returns the outer transaction is rolled back, so no
   writes leak across tests.
3. ``client`` overrides the FastAPI dependency that normally yields a
   fresh session so the same transactional session is reused inside
   request handlers.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import AsyncIterator
from urllib.parse import urlparse, urlunparse

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://spendlens:spendlens@postgres:5432/spendlens_test",
)
os.environ.setdefault("REDIS_URL", "redis://redis:6379/1")
os.environ.setdefault("JWT_SECRET", "test-secret-must-be-at-least-32-characters-long")
os.environ.setdefault("S3_ACCESS_KEY", "spendlens")
os.environ.setdefault("S3_SECRET_KEY", "spendlens-secret")
# The API container talks to the MinIO service on the compose network;
# CI overrides this to ``http://localhost:9000`` before pytest runs.
os.environ.setdefault("S3_ENDPOINT_URL", "http://minio:9000")
os.environ.setdefault("S3_BUCKET", "receipts")


def _admin_url(test_url: str) -> str:
    """Swap the database name for the default ``postgres`` admin DB so we can
    issue CREATE/DROP DATABASE against the server."""
    parsed = urlparse(test_url)
    return urlunparse(parsed._replace(path="/postgres"))


def _db_name(test_url: str) -> str:
    return urlparse(test_url).path.lstrip("/")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _prepare_test_database() -> AsyncIterator[None]:
    """Recreate the test DB and apply migrations once per test session."""
    from sqlalchemy import text

    test_url = os.environ["DATABASE_URL"]
    admin_engine = create_async_engine(_admin_url(test_url), isolation_level="AUTOCOMMIT")
    db_name = _db_name(test_url)

    async with admin_engine.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    # Alembic's env.py spins up its own event loop via asyncio.run, which
    # clashes with the loop pytest-asyncio is already running. Shelling out
    # keeps both happy — the CI job already runs the same command before
    # pytest, so this is a cheap idempotent re-apply locally.
    subprocess.run(
        ["alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": test_url},
    )

    yield


@pytest_asyncio.fixture
async def db_connection() -> AsyncIterator[AsyncConnection]:
    """Per-test connection wrapped in an outer transaction.

    All work inside a test runs inside this transaction. The rollback at
    teardown wipes every insert/update, no matter how many SAVEPOINTs
    the code opens along the way.
    """
    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            yield conn
        finally:
            await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """Session bound to the per-test connection."""
    session_factory = async_sessionmaker(
        bind=db_connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """HTTPX client backed by the FastAPI app with DB DI overridden."""
    from app.api.v1.deps import db_session as db_session_dep
    from app.main import create_app

    app = create_app()

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[db_session_dep] = _override

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()

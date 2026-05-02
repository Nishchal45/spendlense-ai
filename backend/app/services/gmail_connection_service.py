"""CRUD over :class:`GmailConnection`.

Mirrors the shape of the other resource services: ownership in every
``WHERE`` clause, 404 on cross-tenant access, no SQL in the routes.
The only twist is the **upsert** path the OAuth callback uses — a
re-grant of the same Google account replaces the existing row's
encrypted refresh token rather than failing with a unique-constraint
error. That keeps re-consenting idempotent from the user's
perspective ("re-connect" should just work).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.secret_box import encrypt_secret
from app.models.gmail_connection import GmailConnection

log = structlog.get_logger()


class GmailConnectionNotFoundError(Exception):
    """No connection with that id exists for the requesting user."""


async def upsert_connection(
    session: AsyncSession,
    *,
    user_id: UUID,
    google_email: str,
    refresh_token: str,
) -> GmailConnection:
    """Insert a new connection or replace the encrypted token on re-grant.

    Encrypts the refresh token before it ever lands in a parameter
    binding, so even a query log can't surface the plaintext. Uses
    Postgres' ``INSERT ... ON CONFLICT`` so the unique constraint
    on ``(user_id, google_email)`` doubles as the upsert key.
    """
    encrypted = encrypt_secret(refresh_token)

    stmt = (
        pg_insert(GmailConnection)
        .values(
            user_id=user_id,
            google_email=google_email,
            encrypted_refresh_token=encrypted,
        )
        .on_conflict_do_update(
            constraint="uq_gmail_user_account",
            set_={"encrypted_refresh_token": encrypted},
        )
        .returning(GmailConnection)
    )
    result = await session.execute(stmt)
    connection = result.scalar_one()
    await session.commit()
    # Refresh so ``created_at`` / ``updated_at`` are populated for
    # the response. The default load strategy on the returning row
    # doesn't pick up server-side defaults.
    await session.refresh(connection)
    log.info(
        "gmail.connection_upserted",
        user_id=str(user_id),
        connection_id=str(connection.id),
        google_email=google_email,
    )
    return connection


async def list_connections(session: AsyncSession, *, user_id: UUID) -> Sequence[GmailConnection]:
    """All Gmail connections owned by the user, oldest first."""
    rows = (
        await session.execute(
            select(GmailConnection)
            .where(GmailConnection.user_id == user_id)
            .order_by(GmailConnection.created_at.asc())
        )
    ).scalars()
    return rows.all()


async def find_by_google_email(
    session: AsyncSession, *, google_email: str
) -> Sequence[GmailConnection]:
    """Look up every connection for a given Gmail address.

    Used by the Pub/Sub push handler — the push payload identifies
    the user by their Gmail address, not by our internal user id.
    Multiple rows are theoretically possible (two users connect the
    same account), so we return all matches and let the caller fan
    out one Celery task per match.
    """
    rows = (
        await session.execute(
            select(GmailConnection).where(GmailConnection.google_email == google_email)
        )
    ).scalars()
    return rows.all()


async def get_connection(
    session: AsyncSession, *, user_id: UUID, connection_id: UUID
) -> GmailConnection:
    """Fetch one connection. 404-shaped on cross-tenant access."""
    row = (
        await session.execute(
            select(GmailConnection).where(
                GmailConnection.id == connection_id,
                GmailConnection.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise GmailConnectionNotFoundError(str(connection_id))
    return row


async def delete_connection(
    session: AsyncSession, *, user_id: UUID, connection_id: UUID
) -> GmailConnection:
    """Delete and return the row. Returns the deleted row so the
    route can hand the encrypted refresh token to the revoke flow.
    """
    connection = await get_connection(session, user_id=user_id, connection_id=connection_id)
    await session.delete(connection)
    await session.commit()
    log.info(
        "gmail.connection_deleted",
        user_id=str(user_id),
        connection_id=str(connection_id),
    )
    return connection

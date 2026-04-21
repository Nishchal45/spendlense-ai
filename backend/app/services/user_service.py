"""User persistence and authentication logic.

Routers stay thin — all the database reads, writes, and credential
checks happen here so tests can exercise the flow without spinning up
the HTTP layer.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User


class EmailAlreadyRegisteredError(Exception):
    """Raised when trying to register an email that already exists."""


class InvalidCredentialsError(Exception):
    """Raised on login when email is unknown or password doesn't match."""


def _normalise_email(email: str) -> str:
    """Lowercase + strip. Email local-parts are case-sensitive per the RFC but
    every real provider treats them case-insensitively, so we store the
    normalised form to prevent duplicate accounts."""
    return email.strip().lower()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    stmt = select(User).where(User.email == _normalise_email(email))
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    return await session.get(User, user_id)


async def create_user(session: AsyncSession, email: str, password: str) -> User:
    """Persist a new user with a bcrypt-hashed password.

    Raises :class:`EmailAlreadyRegisteredError` if the email is taken. We
    rely on the DB's unique constraint (not a pre-check) so two
    concurrent registrations can't both win the race.
    """
    user = User(email=_normalise_email(email), password_hash=hash_password(password))
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise EmailAlreadyRegisteredError(email) from exc

    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> User:
    """Return the user if the credentials are valid, else raise.

    We always run the bcrypt comparison even when the email is unknown so
    the endpoint's response time doesn't leak whether an email is
    registered.
    """
    user = await get_user_by_email(session, email)
    if user is None:
        # Dummy hash to keep timing constant. Same cost factor as real hashes.
        verify_password(password, "$2b$12$" + "A" * 53)
        raise InvalidCredentialsError()

    if not verify_password(password, user.password_hash):
        raise InvalidCredentialsError()

    return user

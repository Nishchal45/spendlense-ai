"""Shared FastAPI dependencies for the v1 API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import TokenError, decode_access_token
from app.models.user import User
from app.services.user_service import get_user_by_id

# ``tokenUrl`` drives the Swagger "Authorize" button — it doesn't change how
# tokens are received (still Authorization: Bearer ...). Point it at the real
# login route so the interactive docs can mint tokens in one click.
_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{get_settings().api_prefix}/auth/login",
    auto_error=False,
)


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


SessionDep = Annotated[AsyncSession, Depends(db_session)]


async def get_current_user(
    session: SessionDep,
    token: Annotated[str | None, Depends(_oauth2_scheme)],
) -> User:
    """Resolve the bearer token to a ``User`` or raise 401.

    Every protected route takes ``current_user: CurrentUser`` and is done
    — no manual token plumbing.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not token:
        raise credentials_exc

    try:
        claims = decode_access_token(token)
    except TokenError as exc:
        raise credentials_exc from exc

    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise credentials_exc from exc

    user = await get_user_by_id(session, user_id)
    if user is None:
        raise credentials_exc

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

"""Registration, login, and 'who am I' endpoints."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status

from app.api.v1.deps import CurrentUser, SessionDep
from app.core.config import get_settings
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import LoginIn, RegisterIn, TokenOut, UserOut
from app.services.user_service import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    authenticate,
    create_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])
log = structlog.get_logger()


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterIn, session: SessionDep) -> UserOut:
    try:
        user = await create_user(session, email=payload.email, password=payload.password)
    except EmailAlreadyRegisteredError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from exc

    log.info("auth.user_registered", user_id=str(user.id))
    return _user_out(user)


@router.post("/login", response_model=TokenOut)
async def login(payload: LoginIn, session: SessionDep) -> TokenOut:
    try:
        user = await authenticate(session, email=payload.email, password=payload.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from exc

    settings = get_settings()
    token = create_access_token(user.id)
    log.info("auth.user_logged_in", user_id=str(user.id))
    return TokenOut(
        access_token=token,
        expires_in=settings.jwt_access_token_ttl_minutes * 60,
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    return _user_out(current_user)


def _user_out(user: User) -> UserOut:
    """Project a ``User`` ORM row into the wire shape, computing the
    derived ``inbox_address`` from the configured domain.

    The address is computed at the route layer (not the schema) so
    Pydantic's ``model_validate`` doesn't need to reach into
    ``Settings`` — keeping the schema dependency-free makes it
    importable from one-shot scripts and the test harness.
    """
    settings = get_settings()
    return UserOut(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        inbox_token=user.inbox_token,
        inbox_address=f"receipts+{user.inbox_token}@{settings.inbox_email_domain}",
    )

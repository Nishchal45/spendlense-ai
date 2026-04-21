"""Password hashing and JWT issuance.

Kept as pure functions so routes, background tasks, and tests can all
use the same primitives without pulling in FastAPI internals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings

# bcrypt cost factor 12 is the modern default: ~250ms to hash on commodity
# hardware, which makes online credential stuffing expensive without making
# legitimate logins feel sluggish.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


class TokenError(Exception):
    """Raised when a JWT is malformed, expired, or fails signature check."""


def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain``.

    The returned string embeds the algorithm, cost, and salt, so no other
    metadata needs to be stored alongside it.
    """
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time check that ``plain`` matches ``hashed``."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(
    subject: UUID | str,
    expires_in: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Mint a signed JWT for ``subject`` (typically the user id).

    The token carries the standard ``sub``, ``iat``, ``exp`` claims. Extra
    claims can be injected for future scopes/roles without changing the
    call sites.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    ttl = expires_in or timedelta(minutes=settings.jwt_access_token_ttl_minutes)

    claims: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "type": "access",
    }
    if extra_claims:
        claims.update(extra_claims)

    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate ``token``. Raises :class:`TokenError` on failure.

    Callers get a plain dict so they can decide what to do with the claims.
    """
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("type") != "access":
        raise TokenError("unexpected token type")
    if "sub" not in payload:
        raise TokenError("token missing subject")

    return payload

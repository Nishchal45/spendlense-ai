"""Pydantic schemas for the auth surface.

These are the wire contracts. Keep them decoupled from ORM models so
routes can evolve without tripping database-layer validation.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    """Payload for ``POST /auth/register``."""

    email: EmailStr
    # 8-char floor is the OWASP ASVS L1 minimum. Upper bound keeps bcrypt's
    # 72-byte truncation from silently accepting absurdly long inputs.
    password: str = Field(min_length=8, max_length=72)


class LoginIn(BaseModel):
    """Payload for ``POST /auth/login``."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TokenOut(BaseModel):
    """RFC 6749 §5.1-shaped token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until ``access_token`` expires


class UserOut(BaseModel):
    """Public projection of a user — never leaks the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime

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
    """Public projection of a user — never leaks the password hash.

    ``inbox_token`` rides on the wire so the dashboard can show the
    user's forward-to-email address. The token is sensitive (anyone
    with it can fire receipts into the account) but it's also
    meaningless without the configured MX, so the threat model
    aligns with "treat like a long-lived bearer for one narrow
    write surface" — not as bad as the JWT, but not OK in a URL
    either.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    created_at: datetime
    inbox_token: str
    # ``receipts+<inbox_token>@<inbox_email_domain>``. Computed at
    # the route layer so the schema doesn't reach into Settings.
    inbox_address: str

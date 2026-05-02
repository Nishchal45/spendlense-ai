"""Wire contracts for the Gmail OAuth integration surface.

Tokens never appear in any of these — the encrypted refresh token
stays inside the database, and the access token only lives long
enough for the callback to call userinfo. Anything the API returns
to the user is safe to render in the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class GmailConnectURL(BaseModel):
    """Response for ``GET /integrations/gmail/connect``.

    The frontend reads ``url`` and does ``window.location.assign``.
    Returning JSON instead of a 302 keeps the auth-required guard
    sensible — a redirect from a fetch() with credentials would be
    transparent and obscure failures from the caller.
    """

    url: str


class GmailConnectionOut(BaseModel):
    """Public projection of a ``gmail_connections`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    google_email: str
    last_history_id: str | None
    watch_expiration: datetime | None
    created_at: datetime
    updated_at: datetime


class GmailConnectionList(BaseModel):
    """Envelope for ``GET /integrations/gmail``."""

    items: list[GmailConnectionOut]

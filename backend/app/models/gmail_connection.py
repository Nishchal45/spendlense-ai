"""Gmail OAuth connection state.

One row per (user, Google account). The user can in principle
connect multiple Google accounts (work + personal); the unique
constraint on ``(user_id, google_email)`` enforces "no duplicates"
without forcing single-account-only.

Tokens are stored as Fernet ciphertext via
:mod:`app.core.secret_box`. The DB column is ``Text`` so we don't
have to recompute the ciphertext-length cap when ``cryptography``
ships a new ciphertext format.

What this row holds:

* ``encrypted_refresh_token`` — the long-lived Google refresh
  token. Encrypted at rest.
* ``last_history_id`` — Gmail's incremental-sync cursor (see
  :doc:`Gmail API History` docs). When a Pub/Sub push fires, we
  fetch ``users.history.list?startHistoryId=<this>`` to enumerate
  changes since the last sync.
* ``watch_expiration`` — Gmail's ``users.watch`` subscription
  lapses every 7 days. Tracking this lets a Phase 6+ refresher
  task call ``users.watch`` again before it expires.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class GmailConnection(Base, TimestampMixin):
    __tablename__ = "gmail_connections"
    __table_args__ = (
        # One connection per (user, Google account). Reconnecting the
        # same account replaces the row (UPDATE), not a duplicate INSERT.
        UniqueConstraint("user_id", "google_email", name="uq_gmail_user_account"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The Google email address the OAuth consent was granted for.
    # Surfaced in the dashboard so the user can tell which account
    # they connected (and disconnect the right one).
    google_email: Mapped[str] = mapped_column(String(255), nullable=False)

    # Long-lived refresh token, Fernet-encrypted. Use
    # :func:`app.core.secret_box.encrypt_secret` /
    # ``decrypt_secret``; never store plaintext here.
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)

    # Gmail's incremental-sync cursor. NULL until the first
    # ``users.watch`` returns a ``historyId``.
    last_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # When the current ``users.watch`` subscription expires. NULL if
    # we haven't subscribed yet (immediately after consent, before
    # the first ``users.watch`` call). A Phase 6+ refresher will
    # call ``users.watch`` again before this fires.
    watch_expiration: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="gmail_connections")

"""create gmail_connections

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-01 09:30:00.000000

Phase 5.6 Gmail OAuth. One row per (user, Google account):

* ``encrypted_refresh_token`` is Fernet ciphertext, ``Text`` because
  the ciphertext format isn't fixed-width across cryptography
  versions.
* ``last_history_id`` is Gmail's incremental sync cursor — opaque
  string, fits in 64 chars.
* ``watch_expiration`` lets a future refresher task call
  ``users.watch`` again before the 7-day Gmail subscription lapses.
* ``UNIQUE(user_id, google_email)`` so reconnecting the same
  Google account is an UPDATE, not a duplicate INSERT.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gmail_connections",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_email", sa.String(length=255), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("last_history_id", sa.String(length=64), nullable=True),
        sa.Column("watch_expiration", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint("user_id", "google_email", name="uq_gmail_user_account"),
    )
    op.create_index(
        "ix_gmail_connections_user_id",
        "gmail_connections",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_gmail_connections_user_id", table_name="gmail_connections")
    op.drop_table("gmail_connections")

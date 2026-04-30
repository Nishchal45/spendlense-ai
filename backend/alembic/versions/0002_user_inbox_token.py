"""add inbox_token to users

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-30 03:30:00.000000

Phase 5.5 forward-to-email ingestion. Adds a per-user 128-bit hex
token used as the local-part suffix of the user's
``receipts+<token>@inbox.spendlens.app`` forward address. Three
constraints worth flagging in the migration:

* **NOT NULL** is enforced *after* the backfill so existing rows
  don't trip the constraint at CREATE time.
* **UNIQUE** is enforced from the start — every user must have a
  distinct token. Postgres rejects collisions at insert time, which
  pairs with ``secrets.token_hex(16)`` (128 bits of entropy → birthday
  collision is one in 2^64; we'd need a billion users to start
  caring).
* **Indexed** so the inbound webhook can resolve a token to a user in
  one round trip without a scan.
"""

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add nullable first so existing rows survive the column add.
    op.add_column(
        "users",
        sa.Column("inbox_token", sa.String(length=32), nullable=True),
    )

    # Backfill every existing user with a fresh token. We use Python's
    # ``secrets`` module rather than ``gen_random_uuid()`` so the token
    # format stays consistent with what new signups produce.
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id FROM users")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE users SET inbox_token = :token WHERE id = :uid"),
            {"token": secrets.token_hex(16), "uid": row[0]},
        )

    # Now the column is fully populated — flip to NOT NULL and add the
    # unique index. Doing this in one ``ALTER COLUMN`` plus a separate
    # CREATE INDEX keeps the migration idempotent on crash recovery
    # (``IF NOT EXISTS`` would also work but obscures intent).
    op.alter_column("users", "inbox_token", nullable=False)
    op.create_index(
        "ix_users_inbox_token",
        "users",
        ["inbox_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_inbox_token", table_name="users")
    op.drop_column("users", "inbox_token")

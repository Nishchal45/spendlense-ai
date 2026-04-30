"""add external_message_id dedup column to receipts

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-30 03:55:00.000000

Phase 5.5 inbound-email ingestion. The webhook may receive the same
message twice (provider retries, accidental forward-rule loops); the
partial unique index keys dedup on ``(user_id, external_message_id)``
when the column is non-null, which is exactly the rows email created.
Direct uploads leave the column NULL and are unaffected.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "receipts",
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
    )
    # Partial unique — Postgres only enforces the constraint on rows
    # where the column is NOT NULL. Plain UNIQUE would refuse two
    # NULLs (in some Postgres modes) and we'd block second uploads.
    op.create_index(
        "uq_receipts_user_external_message_id",
        "receipts",
        ["user_id", "external_message_id"],
        unique=True,
        postgresql_where=sa.text("external_message_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_receipts_user_external_message_id", table_name="receipts")
    op.drop_column("receipts", "external_message_id")

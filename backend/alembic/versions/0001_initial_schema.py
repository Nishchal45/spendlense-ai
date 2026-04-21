"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-04-20 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


expense_category = postgresql.ENUM(
    "food_dining",
    "groceries",
    "transportation",
    "shopping",
    "entertainment",
    "utilities",
    "healthcare",
    "housing",
    "travel",
    "education",
    "personal",
    "other",
    name="expense_category",
    create_type=False,
)

expense_source = postgresql.ENUM(
    "manual", "receipt", "import", name="expense_source", create_type=False
)

receipt_status = postgresql.ENUM(
    "uploaded",
    "processing",
    "parsed",
    "categorised",
    "failed",
    name="receipt_status",
    create_type=False,
)

ocr_method = postgresql.ENUM("tesseract", "gpt4v", name="ocr_method", create_type=False)

budget_period = postgresql.ENUM("monthly", name="budget_period", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    expense_category.create(bind, checkfirst=True)
    expense_source.create(bind, checkfirst=True)
    receipt_status.create(bind, checkfirst=True)
    ocr_method.create(bind, checkfirst=True)
    budget_period.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer, nullable=False),
        sa.Column("status", receipt_status, nullable=False, server_default="uploaded"),
        sa.Column("ocr_method", ocr_method, nullable=True),
        sa.Column("ocr_confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("parsed_payload", postgresql.JSONB, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_receipts_user_created", "receipts", ["user_id", "created_at"])
    op.create_index("ix_receipts_status", "receipts", ["status"])

    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_name", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("category", expense_category, nullable=False),
        sa.Column("expense_date", sa.Date, nullable=False),
        sa.Column("description", sa.String(1024), nullable=True),
        sa.Column(
            "receipt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("receipts.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("source", expense_source, nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_expenses_user_date", "expenses", ["user_id", "expense_date"])
    op.create_index("ix_expenses_user_category", "expenses", ["user_id", "category"])
    op.create_index("ix_expenses_merchant_name", "expenses", ["merchant_name"])

    op.create_table(
        "line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "expense_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.String(512), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 2), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("total_price", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_line_items_expense_id", "line_items", ["expense_id"])

    op.create_table(
        "budgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", expense_category, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("period", budget_period, nullable=False, server_default="monthly"),
        sa.Column("alert_threshold_pct", sa.Integer, nullable=False, server_default="80"),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "category", "period", name="uq_budget_per_category_period"),
    )

    op.create_table(
        "category_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("merchant_name", sa.String(255), nullable=False),
        sa.Column("category", expense_category, nullable=False),
        sa.Column("occurrence_count", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "last_applied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "merchant_name", name="uq_correction_user_merchant"),
    )


def downgrade() -> None:
    op.drop_table("category_corrections")
    op.drop_table("budgets")
    op.drop_index("ix_line_items_expense_id", table_name="line_items")
    op.drop_table("line_items")
    op.drop_index("ix_expenses_merchant_name", table_name="expenses")
    op.drop_index("ix_expenses_user_category", table_name="expenses")
    op.drop_index("ix_expenses_user_date", table_name="expenses")
    op.drop_table("expenses")
    op.drop_index("ix_receipts_status", table_name="receipts")
    op.drop_index("ix_receipts_user_created", table_name="receipts")
    op.drop_table("receipts")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    budget_period.drop(bind, checkfirst=True)
    ocr_method.drop(bind, checkfirst=True)
    receipt_status.drop(bind, checkfirst=True)
    expense_source.drop(bind, checkfirst=True)
    expense_category.drop(bind, checkfirst=True)

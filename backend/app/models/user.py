import secrets
from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# 32 hex chars = 128 bits of entropy. The inbox token is the
# forward-to-email surface's only authenticator — an attacker who
# guesses someone's token can fire receipts into their account
# (annoying but recoverable), so ``secrets.token_hex`` is the right
# RNG and a wide token is the right hedge. Stored as ``String(32)``
# (not JSON, not an enum) so the inbound-webhook lookup stays a
# straight unique-index hit.
_INBOX_TOKEN_BYTES = 16


def _mint_inbox_token() -> str:
    """Generate a fresh 128-bit hex token for the user's forward address."""
    return secrets.token_hex(_INBOX_TOKEN_BYTES)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Per-user opaque token used as the local-part suffix of the
    # forward-to-email address (``receipts+<token>@...``). Minted at
    # signup, rotatable without changing the user id. Indexed for
    # the inbound webhook's one-shot resolution.
    inbox_token: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, default=_mint_inbox_token
    )

    expenses: Mapped[list["Expense"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    receipts: Mapped[list["Receipt"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    budgets: Mapped[list["Budget"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    corrections: Mapped[list["CategoryCorrection"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# Avoid circular import at runtime but keep type hints resolvable.
from app.models.budget import Budget  # noqa: E402
from app.models.category_correction import CategoryCorrection  # noqa: E402
from app.models.expense import Expense  # noqa: E402
from app.models.receipt import Receipt  # noqa: E402

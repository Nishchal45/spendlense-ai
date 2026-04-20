from uuid import UUID, uuid4

from sqlalchemy import String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

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

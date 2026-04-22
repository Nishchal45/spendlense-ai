"""Wire contracts for the expense CRUD surface.

Kept separate from ORM models so API versioning, computed fields, and
validation rules can evolve without a database migration.

Conventions:

* ``Decimal`` is the canonical money type. We never let JSON floats
  round-trip through a monetary field.
* Amounts are strictly positive. Refunds / credits will be modelled as
  a distinct transaction type in a later phase; conflating them with
  negative-amount expenses would silently break category totals.
* ``currency`` is a 3-letter ISO-4217 code, uppercased on the way in so
  the DB stays canonical regardless of what the client sends.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ExpenseCategory, ExpenseSource

# Numeric(12, 2) in the DB — 10 integer digits + 2 fractional. We mirror
# that at the schema layer so the validation error fires before asyncpg
# raises a less-friendly ``numeric field overflow``.
_AMOUNT_MAX = Decimal("9999999999.99")
_AMOUNT_MIN = Decimal("0.01")
_CURRENCY_CODE_LEN = 3


def _validate_currency(v: str) -> str:
    v = v.strip().upper()
    if len(v) != _CURRENCY_CODE_LEN or not v.isalpha():
        raise ValueError("currency must be a 3-letter ISO-4217 code")
    return v


class ExpenseBase(BaseModel):
    """Fields shared by create / update / read projections."""

    merchant_name: str = Field(min_length=1, max_length=255)
    amount: Decimal = Field(ge=_AMOUNT_MIN, le=_AMOUNT_MAX, decimal_places=2)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    category: ExpenseCategory
    expense_date: date
    description: str | None = Field(default=None, max_length=1024)

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, v: str) -> str:
        return _validate_currency(v)

    @field_validator("merchant_name")
    @classmethod
    def _strip_merchant(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("merchant_name must not be blank")
        return v


class ExpenseCreate(ExpenseBase):
    """Payload for ``POST /expenses``."""


class ExpenseUpdate(BaseModel):
    """Partial update for ``PATCH /expenses/{id}``.

    Every field is optional. ``None`` is ambiguous for ``description``
    (clear the note vs. don't touch it) so we rely on the set-of-fields
    Pydantic exposes via ``model_dump(exclude_unset=True)``.
    """

    merchant_name: str | None = Field(default=None, min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, ge=_AMOUNT_MIN, le=_AMOUNT_MAX, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    category: ExpenseCategory | None = None
    expense_date: date | None = None
    description: str | None = Field(default=None, max_length=1024)

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, v: str | None) -> str | None:
        return None if v is None else _validate_currency(v)

    @field_validator("merchant_name")
    @classmethod
    def _strip_merchant(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("merchant_name must not be blank")
        return v


class ExpenseOut(ExpenseBase):
    """Public projection of an ``Expense`` row."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    source: ExpenseSource
    receipt_id: UUID | None
    created_at: datetime
    updated_at: datetime


class PaginatedExpenses(BaseModel):
    """Cursor-paginated envelope for ``GET /expenses``.

    ``next_cursor`` is opaque to the client — base64url of
    ``(expense_date, id)`` — and is ``None`` when no more rows exist.
    """

    items: list[ExpenseOut]
    next_cursor: str | None = None

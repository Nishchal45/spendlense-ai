"""Unit tests for expense Pydantic schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.enums import ExpenseCategory
from app.schemas.expense import ExpenseCreate, ExpenseUpdate


def _base_payload() -> dict[str, object]:
    return {
        "merchant_name": "Blue Bottle Coffee",
        "amount": "4.75",
        "currency": "usd",
        "category": ExpenseCategory.FOOD_DINING,
        "expense_date": date(2026, 4, 20),
        "description": "Morning coffee",
    }


def test_create_accepts_valid_payload() -> None:
    expense = ExpenseCreate.model_validate(_base_payload())
    assert expense.amount == Decimal("4.75")
    assert expense.currency == "USD"  # normalised
    assert expense.merchant_name == "Blue Bottle Coffee"


def test_create_rejects_zero_amount() -> None:
    payload = _base_payload() | {"amount": "0.00"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_create_rejects_negative_amount() -> None:
    payload = _base_payload() | {"amount": "-1.00"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_create_rejects_three_decimal_places() -> None:
    payload = _base_payload() | {"amount": "1.234"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_create_rejects_overflow_amount() -> None:
    payload = _base_payload() | {"amount": "99999999999.99"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_create_rejects_bad_currency() -> None:
    payload = _base_payload() | {"currency": "US"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)

    payload = _base_payload() | {"currency": "US1"}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_create_strips_whitespace_from_merchant() -> None:
    payload = _base_payload() | {"merchant_name": "  Uber  "}
    expense = ExpenseCreate.model_validate(payload)
    assert expense.merchant_name == "Uber"


def test_create_rejects_blank_merchant() -> None:
    payload = _base_payload() | {"merchant_name": "   "}
    with pytest.raises(ValidationError):
        ExpenseCreate.model_validate(payload)


def test_update_allows_partial_fields() -> None:
    # Only category changes; other fields stay unset so the service knows
    # not to overwrite them.
    update = ExpenseUpdate.model_validate({"category": ExpenseCategory.GROCERIES})
    assert update.model_dump(exclude_unset=True) == {"category": ExpenseCategory.GROCERIES}


def test_update_normalises_currency() -> None:
    update = ExpenseUpdate.model_validate({"currency": "eur"})
    assert update.currency == "EUR"


def test_update_rejects_invalid_amount() -> None:
    with pytest.raises(ValidationError):
        ExpenseUpdate.model_validate({"amount": "-5.00"})

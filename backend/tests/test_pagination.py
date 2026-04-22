"""Unit tests for cursor encoding/decoding."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest

from app.core.pagination import ExpenseCursor, InvalidCursorError


def test_cursor_round_trips() -> None:
    original = ExpenseCursor(expense_date=date(2026, 4, 21), id=uuid4())
    decoded = ExpenseCursor.decode(original.encode())
    assert decoded == original


def test_cursor_is_url_safe_and_unpadded() -> None:
    cursor = ExpenseCursor(expense_date=date(2026, 1, 1), id=uuid4())
    encoded = cursor.encode()
    # No reserved URL characters that would force percent-encoding.
    assert "+" not in encoded
    assert "/" not in encoded
    assert "=" not in encoded


def test_cursor_decodes_missing_padding() -> None:
    # Sanity: even though ``encode`` strips padding, ``decode`` must cope
    # when the cursor comes back through a client that didn't preserve it.
    original = ExpenseCursor(
        expense_date=date(2026, 4, 21),
        id=UUID("12345678-1234-5678-1234-567812345678"),
    )
    encoded_stripped = original.encode().rstrip("=")
    assert ExpenseCursor.decode(encoded_stripped) == original


def test_cursor_rejects_garbage() -> None:
    with pytest.raises(InvalidCursorError):
        ExpenseCursor.decode("not-a-real-cursor-!!!")


def test_cursor_rejects_missing_fields() -> None:
    import base64
    import json

    payload = json.dumps({"d": "2026-04-21"}).encode("utf-8")  # no id
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(InvalidCursorError):
        ExpenseCursor.decode(token)


def test_cursor_rejects_bad_date() -> None:
    import base64
    import json

    payload = json.dumps({"d": "not-a-date", "i": str(uuid4())}).encode("utf-8")
    token = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(InvalidCursorError):
        ExpenseCursor.decode(token)

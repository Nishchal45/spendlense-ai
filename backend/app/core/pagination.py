"""Opaque cursor encoding for keyset pagination.

Why keyset (cursor) instead of ``LIMIT/OFFSET``:

* Offset pagination walks the entire skipped prefix on every page, so
  page-1000 is thousands of times slower than page-1.
* Keyset pagination uses the ``(expense_date DESC, id DESC)`` index as
  a seek, so every page is O(page_size) regardless of depth.
* Stable under concurrent writes — inserting a new row doesn't shift
  the pagination window, which ``OFFSET`` does silently.

Cursor format is base64url(JSON). Base64url keeps it URL-safe without
percent-encoding; JSON keeps it debuggable (``base64 -d`` gives a
readable payload during incident review). The cursor is opaque to
clients — treat the shape as an implementation detail.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from uuid import UUID


class InvalidCursorError(ValueError):
    """Raised when a client passes a cursor that doesn't round-trip."""


@dataclass(frozen=True)
class ExpenseCursor:
    """Keyset position for the expenses list.

    Pairs the sort key (``expense_date``) with a tiebreaker (``id``) so
    same-day rows page deterministically — without ``id`` you can miss
    or duplicate rows when two expenses share a date.
    """

    expense_date: date
    id: UUID

    def encode(self) -> str:
        payload = json.dumps(
            {"d": self.expense_date.isoformat(), "i": str(self.id)},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, raw: str) -> ExpenseCursor:
        try:
            padded = raw + "=" * (-len(raw) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            return cls(
                expense_date=date.fromisoformat(payload["d"]),
                id=UUID(payload["i"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidCursorError("cursor is malformed") from exc

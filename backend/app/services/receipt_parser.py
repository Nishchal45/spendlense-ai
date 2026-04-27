"""Best-effort regex extraction of structured fields from receipt OCR text.

Receipts are a notoriously diverse format — every retailer's POS
spits out a different layout. This module is deliberately *not* a
heuristic engine; it's three small, testable extractors that cover
the obvious cases and surrender (return ``None``) on the rest. The
LLM-fallback branch in PR #D fills the gaps, and the
``category_corrections`` feedback loop in PR #C learns from misses.

Three rules drive every choice here:

1. **Predictable failure mode.** A regex either matches or it
   doesn't. We never half-extract a value or guess. Anything we
   can't be confident about stays ``None`` so the pipeline knows to
   ask the model.
2. **No external classifiers.** Sticking to regex means this code
   runs offline, costs nothing, and is easy to reason about during
   debugging — receipt OCR is dirty enough that the failure
   investigations matter more than the happy path.
3. **One pass over the lines.** Receipts top out at maybe a
   thousand characters; perf isn't a constraint, but readability is.

The return type is intentionally a flat ``ParsedReceipt`` rather than
a nested model — JSONB storage is straightforward, and the wire shape
in Phase 7 will be derived from this.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Receipts cover the calendar year (US/EU), full-year dates only, and
# the long-form month name appears on email receipts. Order matters
# only for ambiguous strings — we take the first match.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),  # ISO-8601
    re.compile(r"\b(\d{2}/\d{2}/\d{4})\b"),  # MM/DD/YYYY (US)
    re.compile(r"\b(\d{2}-\d{2}-\d{4})\b"),  # MM-DD-YYYY
    re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b"),  # DD.MM.YYYY (EU)
    re.compile(
        r"\b("
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
        r"\s+\d{1,2},?\s+\d{4}"
        r")\b",
        re.IGNORECASE,
    ),
)

_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%B %d, %Y",
    "%B %d %Y",
)

# Currency-shaped amount: optional symbol/code, then 1–4 digits, optional
# thousand-separator commas, then a *required* two-decimal cents block.
# The mandatory ``\.\d{2}`` keeps us from matching phone numbers, item
# codes, or zip codes — which would otherwise dominate any "biggest
# number on the page" fallback.
_AMOUNT_RE = re.compile(
    r"(?:[$£€]|USD|GBP|EUR)?\s*(\d{1,4}(?:,\d{3})*\.\d{2})\b",
    re.IGNORECASE,
)

# Total-line keywords, ordered most-specific-first. "Grand total"
# wins over "total" so we don't match the prefix when both are present.
_TOTAL_KEYWORDS: tuple[str, ...] = (
    "grand total",
    "amount due",
    "total due",
    "balance due",
    "total",
)

# How many leading lines to inspect for a merchant name. Receipts
# almost always lead with the business name — a five-line window
# covers logos, taglines, and the address block without dragging in
# line items.
_MERCHANT_LINE_WINDOW = 5

# Maximum digit ratio for a merchant candidate. Lines that are
# >40% digits are addresses, phone numbers, or zip codes.
_MERCHANT_MAX_DIGIT_RATIO = 0.4
_MERCHANT_MIN_LEN = 2


@dataclass
class ParsedReceipt:
    """Structured fields extracted from a receipt's OCR text.

    All fields default to ``None``/empty because partial extraction is
    the common case. The pipeline persists this dict to
    ``receipts.parsed_payload`` (JSONB) verbatim.
    """

    merchant: str | None = None
    total: Decimal | None = None
    transaction_date: date | None = None
    line_items: list[dict[str, Any]] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        """Convert to a JSON-serialisable dict for JSONB storage.

        ``Decimal`` and ``date`` round-trip as strings so the column
        stays portable across drivers (asyncpg JSONB and the test
        suite's text dumps both stringify cleanly).
        """
        payload = asdict(self)
        payload["total"] = str(self.total) if self.total is not None else None
        payload["transaction_date"] = (
            self.transaction_date.isoformat() if self.transaction_date else None
        )
        return payload


def parse_receipt_text(text: str) -> ParsedReceipt:
    """Extract structured fields from raw OCR text. Best-effort."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ParsedReceipt(
        merchant=_extract_merchant(lines),
        total=_extract_total(lines),
        transaction_date=_extract_date(text),
    )


def _extract_merchant(lines: list[str]) -> str | None:
    """Return the first plausible merchant line in the lead-in window.

    Two cheap rejection rules in priority order:

    1. **Lines that start with a digit.** Almost every address and
       phone number on a receipt begins numerically (street number,
       area code) — the merchant name almost never does.
    2. **Lines that are >40% digits.** Catches the "94102 San Jose"
       zip-plus-city pattern that slips past rule 1.
    """
    for line in lines[:_MERCHANT_LINE_WINDOW]:
        if len(line) < _MERCHANT_MIN_LEN:
            continue
        if line[0].isdigit():
            continue
        digit_ratio = sum(c.isdigit() for c in line) / len(line)
        if digit_ratio > _MERCHANT_MAX_DIGIT_RATIO:
            continue
        return line
    return None


_KEYWORD_RES: tuple[re.Pattern[str], ...] = tuple(
    # ``\b`` boundaries are critical — without them ``total`` matches
    # inside ``subtotal``, which is a different number entirely.
    re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
    for kw in _TOTAL_KEYWORDS
)


def _extract_total(lines: list[str]) -> Decimal | None:
    """Find the receipt total.

    Strategy: scan for the highest-priority total-line keyword and
    take the right-most amount on that line (POS layouts put the
    label on the left, the number on the right). Falls back to the
    largest currency-shaped amount on the page if no keyword matches —
    not perfect, but better than ``None`` when the keyword is mangled
    by OCR (a frequent failure on phone photos).
    """
    for keyword_re in _KEYWORD_RES:
        for line in lines:
            if keyword_re.search(line):
                amounts = _AMOUNT_RE.findall(line)
                if amounts:
                    return _to_decimal(amounts[-1])

    # Fallback path. Every amount on the page, take the largest.
    all_amounts = [_to_decimal(match) for line in lines for match in _AMOUNT_RE.findall(line)]
    return max(all_amounts) if all_amounts else None


def _extract_date(text: str) -> date | None:
    """Return the first parseable date in any supported format."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            parsed = _try_parse_date(match.group(1))
            if parsed is not None:
                return parsed
    return None


def _to_decimal(amount_str: str) -> Decimal:
    return Decimal(amount_str.replace(",", ""))


def _try_parse_date(s: str) -> date | None:
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

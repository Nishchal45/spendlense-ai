"""Unit tests for the receipt parser.

These run in milliseconds — pure-python regex over hand-crafted text
samples. They exist to lock in the parser's behaviour against the
small set of layout patterns the regexes cover, so a future tweak
that "improves" one rule doesn't silently regress another.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.receipt_parser import ParsedReceipt, parse_receipt_text


class TestMerchantExtraction:
    def test_first_text_line_wins(self) -> None:
        text = "Blue Bottle Coffee\n123 Main St\n555-1234\n"
        assert parse_receipt_text(text).merchant == "Blue Bottle Coffee"

    def test_skips_address_lines(self) -> None:
        # First line is an address (mostly digits) — parser should
        # walk past it to the actual name.
        text = "12345 Industrial Pkwy\nAcme Hardware\nSan Jose, CA\n"
        assert parse_receipt_text(text).merchant == "Acme Hardware"

    def test_skips_phone_only_line(self) -> None:
        text = "555-867-5309\nPhilz Coffee\n"
        assert parse_receipt_text(text).merchant == "Philz Coffee"

    def test_returns_none_when_blank(self) -> None:
        assert parse_receipt_text("\n   \n").merchant is None


class TestTotalExtraction:
    def test_grand_total_beats_subtotal(self) -> None:
        text = "Subtotal $10.00\nTax $0.85\nGrand Total $10.85\n"
        assert parse_receipt_text(text).total == Decimal("10.85")

    def test_amount_due_keyword(self) -> None:
        text = "Amount Due: $42.50\n"
        assert parse_receipt_text(text).total == Decimal("42.50")

    def test_total_with_currency_symbol(self) -> None:
        text = "TOTAL  £15.99\n"
        assert parse_receipt_text(text).total == Decimal("15.99")

    def test_thousand_separator(self) -> None:
        text = "TOTAL $1,234.56\n"
        assert parse_receipt_text(text).total == Decimal("1234.56")

    def test_falls_back_to_largest_amount(self) -> None:
        # No keyword anywhere — pick the biggest currency-shaped number.
        text = "Coffee 4.75\nCookie 2.50\nTip 1.25\n"
        assert parse_receipt_text(text).total == Decimal("4.75")

    def test_ignores_non_currency_digits(self) -> None:
        # Phone numbers / zip codes shouldn't masquerade as totals —
        # the regex requires the ``.NN`` cents block.
        text = "Phone: 5551234567\nZip 94102\n"
        assert parse_receipt_text(text).total is None


class TestDateExtraction:
    def test_iso_format(self) -> None:
        assert parse_receipt_text("Date: 2026-04-25").transaction_date == date(2026, 4, 25)

    def test_us_slash_format(self) -> None:
        assert parse_receipt_text("Date: 04/25/2026").transaction_date == date(2026, 4, 25)

    def test_eu_dot_format(self) -> None:
        assert parse_receipt_text("25.04.2026 14:32").transaction_date == date(2026, 4, 25)

    def test_long_form_month(self) -> None:
        assert parse_receipt_text("April 25, 2026").transaction_date == date(2026, 4, 25)

    def test_short_month(self) -> None:
        assert parse_receipt_text("Apr 25, 2026").transaction_date == date(2026, 4, 25)

    def test_returns_none_when_absent(self) -> None:
        assert parse_receipt_text("no dates here").transaction_date is None


class TestEndToEnd:
    def test_realistic_receipt(self) -> None:
        text = (
            "Blue Bottle Coffee\n"
            "315 Linden St\n"
            "San Francisco CA 94102\n"
            "\n"
            "Date: 2026-04-25  14:32\n"
            "\n"
            "Cappuccino       4.75\n"
            "Croissant        3.50\n"
            "\n"
            "Subtotal         8.25\n"
            "Tax              0.74\n"
            "Total            8.99\n"
        )
        parsed = parse_receipt_text(text)
        assert parsed.merchant == "Blue Bottle Coffee"
        assert parsed.total == Decimal("8.99")
        assert parsed.transaction_date == date(2026, 4, 25)


class TestJsonable:
    def test_round_trips_decimal_and_date(self) -> None:
        parsed = ParsedReceipt(
            merchant="Acme",
            total=Decimal("9.99"),
            transaction_date=date(2026, 4, 25),
        )
        payload = parsed.to_jsonable()
        assert payload["merchant"] == "Acme"
        assert payload["total"] == "9.99"
        assert payload["transaction_date"] == "2026-04-25"
        assert payload["line_items"] == []

    def test_handles_nones(self) -> None:
        payload = ParsedReceipt().to_jsonable()
        assert payload["merchant"] is None
        assert payload["total"] is None
        assert payload["transaction_date"] is None

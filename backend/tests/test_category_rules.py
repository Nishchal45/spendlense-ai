"""Unit tests for the static merchant→category rule map.

These run in microseconds — pure-python substring lookup. They exist
to lock in priority ordering (specifically: substring patterns can
shadow each other if put in the wrong order) and to document the
default classification each merchant gets, so a future "improve the
rules" PR can't silently regress an established expectation.
"""

from __future__ import annotations

from app.models.enums import ExpenseCategory
from app.services.category_rules import lookup_rule


class TestRuleHits:
    def test_starbucks_is_food_dining(self) -> None:
        assert lookup_rule("Starbucks #1234 San Francisco") == ExpenseCategory.FOOD_DINING

    def test_uber_eats_beats_uber(self) -> None:
        # ``"uber eats"`` MUST come before ``"uber"`` in the rule list,
        # otherwise the ride-share rule swallows food-delivery charges.
        assert lookup_rule("UBER EATS  Burrito") == ExpenseCategory.FOOD_DINING

    def test_uber_alone_is_transportation(self) -> None:
        assert lookup_rule("UBER  TRIP Help") == ExpenseCategory.TRANSPORTATION

    def test_whole_foods_is_groceries(self) -> None:
        assert lookup_rule("Whole Foods Market") == ExpenseCategory.GROCERIES

    def test_netflix_is_entertainment(self) -> None:
        assert lookup_rule("Netflix.com") == ExpenseCategory.ENTERTAINMENT

    def test_amazon_is_shopping(self) -> None:
        assert lookup_rule("Amazon.com Marketplace") == ExpenseCategory.SHOPPING

    def test_pge_is_utilities(self) -> None:
        assert lookup_rule("PG&E Auto-Pay") == ExpenseCategory.UTILITIES

    def test_airbnb_is_travel(self) -> None:
        assert lookup_rule("Airbnb * 4-night Stay") == ExpenseCategory.TRAVEL

    def test_cvs_is_healthcare(self) -> None:
        assert lookup_rule("CVS Pharmacy") == ExpenseCategory.HEALTHCARE


class TestRuleMisses:
    def test_unknown_merchant_returns_none(self) -> None:
        assert lookup_rule("Some Random Brewing Co") is None

    def test_empty_string_returns_none(self) -> None:
        assert lookup_rule("") is None


class TestCaseInsensitivity:
    def test_upper_lower_mixed_all_match(self) -> None:
        assert lookup_rule("STARBUCKS") == ExpenseCategory.FOOD_DINING
        assert lookup_rule("starbucks") == ExpenseCategory.FOOD_DINING
        assert lookup_rule("StArBuCkS") == ExpenseCategory.FOOD_DINING

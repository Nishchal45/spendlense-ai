"""Static rule-based merchant→category mapping.

The first line of categorisation defence: a hand-curated substring map
that runs offline, costs nothing, and covers the merchants that show
up on every receipt of every English-speaking user. Three reasons it
exists alongside the LLM fallback in :mod:`app.services.categorisation`:

1. **Latency.** ``"starbucks"`` doesn't need a 300 ms round-trip to a
   model. Most receipts categorise in microseconds.
2. **Predictability.** The product needs to behave the same when the
   API key is missing or the model is down. The rule path is the
   floor.
3. **Test surface.** Each rule is a single substring + category — easy
   to assert, easy to read, easy to extend.

The rules are deliberately *not* an exhaustive merchant catalogue —
that's what the LLM is for. The map covers high-frequency merchants
(coffee chains, ride-share, streaming) where the cost-per-correctness
trade-off favours a static rule.

**Order matters.** Substrings are checked top-to-bottom and the first
match wins. ``"uber eats"`` must come before ``"uber"`` so a food-
delivery charge doesn't get filed under transportation.
"""

from __future__ import annotations

from app.models.enums import ExpenseCategory

# (substring, category) pairs. Lowercased. Ordered most-specific first.
_RULES: tuple[tuple[str, ExpenseCategory], ...] = (
    # --- FOOD_DINING -------------------------------------------------
    # Substring "uber eats" is checked before "uber" further down so a
    # food-delivery line doesn't fall into TRANSPORTATION.
    ("uber eats", ExpenseCategory.FOOD_DINING),
    ("ubereats", ExpenseCategory.FOOD_DINING),
    ("doordash", ExpenseCategory.FOOD_DINING),
    ("grubhub", ExpenseCategory.FOOD_DINING),
    ("postmates", ExpenseCategory.FOOD_DINING),
    ("starbucks", ExpenseCategory.FOOD_DINING),
    ("blue bottle", ExpenseCategory.FOOD_DINING),
    ("philz", ExpenseCategory.FOOD_DINING),
    ("peet's", ExpenseCategory.FOOD_DINING),
    ("peets coffee", ExpenseCategory.FOOD_DINING),
    ("dunkin", ExpenseCategory.FOOD_DINING),
    ("chipotle", ExpenseCategory.FOOD_DINING),
    ("mcdonald", ExpenseCategory.FOOD_DINING),
    ("subway", ExpenseCategory.FOOD_DINING),
    ("panera", ExpenseCategory.FOOD_DINING),
    ("sweetgreen", ExpenseCategory.FOOD_DINING),
    ("chick-fil-a", ExpenseCategory.FOOD_DINING),
    ("taco bell", ExpenseCategory.FOOD_DINING),
    # --- GROCERIES ---------------------------------------------------
    # Big-box retailers (Costco, Walmart, Target) are the perennially
    # hard call — a single trip mixes groceries and household goods.
    # We default to the most-frequent receipt type per merchant; the
    # ``category_corrections`` write-back lets a user override locally.
    ("whole foods", ExpenseCategory.GROCERIES),
    ("trader joe", ExpenseCategory.GROCERIES),
    ("safeway", ExpenseCategory.GROCERIES),
    ("kroger", ExpenseCategory.GROCERIES),
    ("aldi", ExpenseCategory.GROCERIES),
    ("publix", ExpenseCategory.GROCERIES),
    ("instacart", ExpenseCategory.GROCERIES),
    ("costco", ExpenseCategory.GROCERIES),
    # --- TRANSPORTATION ---------------------------------------------
    # ``"uber"`` lives below ``"uber eats"`` deliberately — see header.
    ("uber", ExpenseCategory.TRANSPORTATION),
    ("lyft", ExpenseCategory.TRANSPORTATION),
    ("shell", ExpenseCategory.TRANSPORTATION),
    ("chevron", ExpenseCategory.TRANSPORTATION),
    ("exxon", ExpenseCategory.TRANSPORTATION),
    ("bart", ExpenseCategory.TRANSPORTATION),
    ("muni", ExpenseCategory.TRANSPORTATION),
    ("caltrain", ExpenseCategory.TRANSPORTATION),
    ("amtrak", ExpenseCategory.TRANSPORTATION),
    # --- ENTERTAINMENT ----------------------------------------------
    ("netflix", ExpenseCategory.ENTERTAINMENT),
    ("spotify", ExpenseCategory.ENTERTAINMENT),
    ("hulu", ExpenseCategory.ENTERTAINMENT),
    ("disney+", ExpenseCategory.ENTERTAINMENT),
    ("amc theatres", ExpenseCategory.ENTERTAINMENT),
    ("ticketmaster", ExpenseCategory.ENTERTAINMENT),
    ("stubhub", ExpenseCategory.ENTERTAINMENT),
    # --- SHOPPING ---------------------------------------------------
    ("amazon", ExpenseCategory.SHOPPING),
    ("target", ExpenseCategory.SHOPPING),
    ("walmart", ExpenseCategory.SHOPPING),
    ("nike", ExpenseCategory.SHOPPING),
    ("apple store", ExpenseCategory.SHOPPING),
    ("best buy", ExpenseCategory.SHOPPING),
    # --- UTILITIES --------------------------------------------------
    ("comcast", ExpenseCategory.UTILITIES),
    ("xfinity", ExpenseCategory.UTILITIES),
    ("pg&e", ExpenseCategory.UTILITIES),
    ("at&t", ExpenseCategory.UTILITIES),
    ("verizon", ExpenseCategory.UTILITIES),
    ("t-mobile", ExpenseCategory.UTILITIES),
    # --- TRAVEL -----------------------------------------------------
    ("airbnb", ExpenseCategory.TRAVEL),
    ("united airlines", ExpenseCategory.TRAVEL),
    ("delta air", ExpenseCategory.TRAVEL),
    ("southwest", ExpenseCategory.TRAVEL),
    ("marriott", ExpenseCategory.TRAVEL),
    ("hilton", ExpenseCategory.TRAVEL),
    ("hyatt", ExpenseCategory.TRAVEL),
    # --- HEALTHCARE -------------------------------------------------
    ("cvs", ExpenseCategory.HEALTHCARE),
    ("walgreens", ExpenseCategory.HEALTHCARE),
    ("kaiser", ExpenseCategory.HEALTHCARE),
)


def lookup_rule(merchant: str) -> ExpenseCategory | None:
    """Return the category for ``merchant`` from the rule map, or ``None``.

    Case-insensitive substring match. The first rule that hits wins —
    declaration order in :data:`_RULES` is the priority order.
    """
    needle = merchant.lower()
    for pattern, category in _RULES:
        if pattern in needle:
            return category
    return None

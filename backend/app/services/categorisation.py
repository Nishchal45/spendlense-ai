"""Pick a category for a merchant, using every signal we have.

Priority chain (highest to lowest):

1. **User corrections.** If this user has previously corrected an
   expense for this merchant via ``PATCH /expenses/{id}``, that wins
   forever. The whole point of the feedback loop is that user fixes
   are sticky.
2. **Static rule map.** Hand-curated substring → category lookup.
   Offline, deterministic, free.
3. **LLM classifier.** ``gpt-4o-mini`` when an OpenAI key is
   configured. Skipped silently when not — self-hosted users without
   a key still get rules + corrections.
4. **`OTHER`.** Floor.

The function returns a category, never raises, never blocks on
anything beyond its own DB query and (optionally) one LLM round-trip.
That keeps the worker task that calls it simple: it gets a category
back and writes the row.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.category_correction import CategoryCorrection
from app.models.enums import ExpenseCategory
from app.services.category_rules import lookup_rule
from app.services.llm_client import categorise_with_llm

log = structlog.get_logger()


def normalise_merchant(merchant: str) -> str:
    """Canonical form used for both correction lookups and writes.

    Lowercasing + whitespace strip is enough for first cut. Stripping
    POS-style "#1234" suffixes is a future improvement (different
    Starbucks locations should share a category) — not paying for
    that complexity until the data shows it matters.
    """
    return merchant.strip().lower()


async def categorise_merchant(
    session: AsyncSession,
    *,
    user_id: UUID,
    merchant: str | None,
) -> ExpenseCategory:
    """Return the best-guess category for a receipt's merchant."""
    if not merchant:
        # Parser couldn't find a merchant; the LLM has nothing to work
        # with either. ``OTHER`` keeps the expense visible without
        # silently misfiling it.
        return ExpenseCategory.OTHER

    normalised = normalise_merchant(merchant)

    # 1. Correction lookup — user fix beats every other signal.
    correction = (
        await session.execute(
            select(CategoryCorrection).where(
                CategoryCorrection.user_id == user_id,
                CategoryCorrection.merchant_name == normalised,
            )
        )
    ).scalar_one_or_none()
    if correction is not None:
        log.debug(
            "categorise.correction_hit",
            merchant=normalised,
            category=correction.category.value,
        )
        return correction.category

    # 2. Static rules — the offline floor.
    rule_category = lookup_rule(normalised)
    if rule_category is not None:
        return rule_category

    # 3. LLM — best-effort, returns None on any failure.
    settings = get_settings()
    if settings.openai_api_key:
        llm_category = await categorise_with_llm(merchant, model=settings.openai_model_categorise)
        if llm_category is not None:
            return llm_category

    # 4. Floor.
    return ExpenseCategory.OTHER

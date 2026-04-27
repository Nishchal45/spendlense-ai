"""Thin async wrapper around the OpenAI client for categorisation.

The categoriser only ever asks the LLM "which category does this
merchant belong to?" — a single classification call against a closed
label set. This module isolates that one prompt + response shape so
the calling code (:mod:`app.services.categorisation`) doesn't have to
think about API errors, parse failures, or whether the API key is
even configured.

Failure model: this function never raises into its caller. On any
problem (no key, network error, the model returns something that
isn't one of our enum values) it returns ``None`` and the categoriser
falls through to ``ExpenseCategory.OTHER``. Categorisation is a
best-effort signal — never the right thing to fail an OCR pipeline
over.
"""

from __future__ import annotations

import structlog
from openai import AsyncOpenAI, OpenAIError

from app.core.config import get_settings
from app.models.enums import ExpenseCategory

log = structlog.get_logger()


_CATEGORY_VALUES = ", ".join(c.value for c in ExpenseCategory)

# Locked low-temperature classification prompt. The model is told
# *exactly* the legal output set and given a fallback (``other``) so we
# never have to deal with "I'm not sure" in free-text. The instruction
# to reply with only the value (not a sentence, not JSON) keeps token
# usage minimal.
_SYSTEM_PROMPT = (
    "You are a strict expense classifier.\n"
    f"Given a merchant name, reply with EXACTLY ONE of: {_CATEGORY_VALUES}\n"
    "Reply with only the category value (lowercase, snake_case), "
    "nothing else. If you genuinely cannot tell, reply: other"
)

# Plenty of room for any of our category values; small enough that a
# misbehaving model can't burn an unbounded number of output tokens.
_MAX_OUTPUT_TOKENS = 16


async def categorise_with_llm(merchant: str, *, model: str) -> ExpenseCategory | None:
    """Ask the configured OpenAI model to classify ``merchant``.

    Returns the matching :class:`ExpenseCategory` or ``None`` on any
    failure path. Caller treats ``None`` as "no opinion" and falls
    back to a default.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        # Self-hosted users without a key get the rule-based path only.
        # Logging at debug — this isn't an error.
        log.debug("categorise.llm_skipped_no_key", merchant=merchant)
        return None

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": merchant},
            ],
            temperature=0,
            max_tokens=_MAX_OUTPUT_TOKENS,
        )
    except OpenAIError as exc:
        # Network blip, rate limit, auth failure — none of these should
        # take down the pipeline. Log so ops sees a trend.
        log.warning("categorise.llm_error", merchant=merchant, error=str(exc))
        return None

    content = (response.choices[0].message.content or "").strip().lower()
    try:
        return ExpenseCategory(content)
    except ValueError:
        # Model hallucinated a category we don't have. Logging the
        # value so we can spot if a real label is consistently being
        # missed (signal for adding it to the enum, not a bug here).
        log.warning("categorise.llm_unknown_value", merchant=merchant, content=content)
        return None

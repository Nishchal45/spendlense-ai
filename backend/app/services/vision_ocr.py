"""GPT-4V vision-based OCR fallback for low-confidence receipts.

When Tesseract returns text below our confidence threshold (the
default is 60.0 on the 0-100 mean-per-word scale), we hand the same
image to a vision-language model that's much better at:

* Faded thermal-paper receipts where the contrast collapsed
* Phone photos taken at an angle / in poor lighting
* Handwritten receipts (small businesses, taxi receipts)
* Layouts Tesseract chokes on (multi-column, watermarks)

The vision model returns *structured fields directly* — there's no
"OCR text → regex parser" round trip. The model both reads the
receipt and extracts the fields in one call. That's why the function
returns a :class:`ParsedReceipt`, not a raw text + confidence pair
like Tesseract.

Failure model: same as the categorisation LLM. No key, network
error, or response-parse failure all return ``None`` and the caller
keeps the Tesseract result. Vision is an upgrade path, never a
hard dependency.
"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import structlog
from openai import AsyncOpenAI, OpenAIError

from app.core.config import get_settings
from app.services.receipt_parser import ParsedReceipt

log = structlog.get_logger()


# JSON-mode prompt — the model is told the exact schema to return so
# we don't have to deal with prose-wrapped output. ``temperature=0``
# locks the output to the most likely tokens; for a structured-extract
# task that's the right knob.
_SYSTEM_PROMPT = (
    "You are a receipt data extractor. Look at the attached receipt "
    "image and return ONLY a JSON object with these keys:\n"
    "  - merchant: string or null\n"
    "  - total: string of the form '12.34' (the final amount due) or null\n"
    "  - transaction_date: ISO-8601 date 'YYYY-MM-DD' or null\n"
    "Use null for any field you genuinely cannot read. "
    "Do not include any other keys. Do not wrap in prose."
)

# Cap output tokens — every legitimate response fits in well under
# 200 tokens. A misbehaving model can't run away.
_MAX_OUTPUT_TOKENS = 200


async def extract_with_vision(
    *, image_bytes: bytes, image_mime: str, model: str
) -> ParsedReceipt | None:
    """Call the vision model. Return a parsed receipt or ``None`` on failure.

    ``image_mime`` must be the actual content type — the OpenAI API
    requires it inline with the data URL.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        log.debug("vision.skipped_no_key")
        return None

    data_url = _to_data_url(image_bytes, image_mime)
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract the fields."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            temperature=0,
            max_tokens=_MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
        )
    except OpenAIError as exc:
        log.warning("vision.api_error", error=str(exc))
        return None

    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        # The ``response_format=json_object`` flag should make this
        # unreachable, but we don't want a malformed response to crash
        # the worker. Log so ops sees the trend.
        log.warning("vision.parse_error", content=content[:200])
        return None

    return _payload_to_parsed_receipt(payload)


def _to_data_url(image_bytes: bytes, mime: str) -> str:
    """Encode raw bytes as a data: URL the OpenAI API accepts."""
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _payload_to_parsed_receipt(payload: dict[str, object]) -> ParsedReceipt:
    """Coerce model output into a strict :class:`ParsedReceipt`.

    Each field is wrapped in a try/except — a partially-bad response
    (good merchant, garbage date) still yields useful data on the
    fields the model got right. The pipeline already tolerates
    ``None`` per field; that's the design surface we lean on here.
    """
    merchant = _to_optional_str(payload.get("merchant"))

    total: Decimal | None = None
    total_raw = payload.get("total")
    if isinstance(total_raw, str):
        try:
            total = Decimal(total_raw)
        except InvalidOperation:
            total = None

    txn_date: date | None = None
    date_raw = payload.get("transaction_date")
    if isinstance(date_raw, str):
        try:
            txn_date = datetime.strptime(date_raw, "%Y-%m-%d").date()
        except ValueError:
            txn_date = None

    return ParsedReceipt(
        merchant=merchant,
        total=total,
        transaction_date=txn_date,
    )


def _to_optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None

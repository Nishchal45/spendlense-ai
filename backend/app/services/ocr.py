"""Tesseract OCR wrapper.

Returns both the recognised text *and* a per-call mean confidence
score so the pipeline can decide whether to fall back to a
vision-language model (PR #D). ``image_to_data`` is what gives us the
per-word confidences; ``image_to_string`` reads cleaner for the parser
because it preserves Tesseract's own line/whitespace heuristics.

Tesseract itself is a Debian package — installed in the API
Dockerfile. The ``pytesseract`` Python binding shells out to that
binary, so the worker container needs the binary on ``PATH``. Local
dev gets it through the same image; production prod will install it
in whatever base image lands in Phase 8.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import pytesseract
from PIL import Image


@dataclass(frozen=True)
class OcrResult:
    """Output of a single OCR pass.

    ``mean_confidence`` is on the 0–100 scale Tesseract uses
    natively. ``-1`` entries (Tesseract's "I couldn't read this
    block" sentinel) are filtered out before averaging — including
    them would drag the mean below the actual quality of the text we
    *did* read.
    """

    text: str
    mean_confidence: float


def run_tesseract(img: Image.Image, *, lang: str = "eng") -> OcrResult:
    """Run Tesseract on ``img`` and return text + mean confidence."""
    text: str = pytesseract.image_to_string(img, lang=lang)

    data = cast(
        dict[str, Any],
        pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT),
    )
    confidences = [int(c) for c in data["conf"] if str(c) != "-1" and int(c) >= 0]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    return OcrResult(text=text, mean_confidence=mean_confidence)

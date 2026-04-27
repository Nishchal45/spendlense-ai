"""Image-bytes → PIL Image normalisation for the OCR pipeline.

The receipts endpoint accepts JPEG, PNG, WEBP, and HEIC uploads.
Pillow decodes the first three natively; HEIC needs the
``pillow-heif`` opener registered before ``Image.open`` will touch
it. We register exactly once at import time so the worker doesn't
pay the cost on every task and so test imports don't reorder
unexpectedly.

The "preprocess for OCR" step is deliberately minimal: convert to
grayscale and hand off. Aggressive deskew/threshold/denoise pipelines
help marginal phone photos but actively hurt clean scans, and the
Tesseract LSTM engine is robust enough that a simple grayscale pass
wins on average across the receipts we see. PR #D's GPT-4V fallback
is the right place to spend complexity for the genuinely-bad images.
"""

from __future__ import annotations

from io import BytesIO

import pillow_heif
from PIL import Image

# Register HEIC support globally. ``register_heif_opener`` is
# idempotent — pillow-heif checks an internal flag — so there's no
# harm in importing this module multiple times.
pillow_heif.register_heif_opener()


def load_image(body: bytes) -> Image.Image:
    """Decode receipt bytes to an RGB PIL image.

    Raises whatever Pillow raises (typically ``UnidentifiedImageError``)
    on malformed input. The caller — the OCR task — wraps that into a
    ``failed`` status update so the receipt row records *why* it
    couldn't be read.
    """
    # ``Image.open`` returns the format-specific ``ImageFile``
    # subclass; calling ``.convert`` widens it to plain ``Image``.
    # Always-convert ensures the return type lines up with the
    # annotation regardless of input format.
    src = Image.open(BytesIO(body))
    return src.convert("RGB")


def for_ocr(img: Image.Image) -> Image.Image:
    """Return a grayscale copy ready for Tesseract."""
    return img.convert("L")

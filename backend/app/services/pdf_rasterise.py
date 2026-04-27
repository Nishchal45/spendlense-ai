"""PDF → PIL.Image rasterisation for the OCR pipeline.

Email receipts (Uber, Amazon, airline confirmations) routinely arrive
as PDF rather than image. ``pdf2image`` shells out to ``pdftoppm``
from the Debian ``poppler-utils`` package — installed in the
:doc:`Dockerfile` and the CI workflow — to turn the bytes into a
single-page PIL image we can hand to the same Tesseract path the
JPEG/PNG branch uses.

We rasterise **only the first page**. The vast majority of receipt
PDFs are single-page; the long tail (multi-page hotel folios,
itemised B2B invoices) we'll handle when a user complains. Going
multi-page now would mean either OCRing every page (cost) or
heuristically picking a "totals" page (complexity), both of which
are premature given how seldom they appear.

DPI is dialled to 200 — high enough that small print stays legible
to Tesseract, low enough that rasterising a phone-emailed PDF stays
under a second of CPU on the worker.
"""

from __future__ import annotations

from pdf2image import convert_from_bytes
from PIL.Image import Image

# 200 DPI is the sweet spot in our spot-checks: 150 DPI lost the
# small-print line items on a typical Uber Eats PDF, 300 DPI was
# 2x slower for no readability gain.
_RASTER_DPI = 200


class PdfRasterError(Exception):
    """The PDF couldn't be rasterised. Caller marks the row failed."""


def rasterise_first_page(body: bytes) -> Image:
    """Return the first page of ``body`` as an RGB :class:`PIL.Image`.

    Raises :class:`PdfRasterError` on a malformed PDF — the caller
    should surface that as a domain error so the row lands in the
    ``failed`` state rather than triggering Celery retries (a broken
    PDF doesn't get better on retry).
    """
    try:
        pages = convert_from_bytes(body, dpi=_RASTER_DPI, first_page=1, last_page=1)
    except Exception as exc:  # noqa: BLE001 — pdf2image surfaces multiple exception types
        raise PdfRasterError(f"pdf2image failed: {exc}") from exc

    if not pages:
        # Defensive — convert_from_bytes returning an empty list
        # without raising would be a poppler-utils bug, but we've
        # seen weirder things in the wild.
        raise PdfRasterError("PDF had no rasterisable pages")

    image = pages[0]
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image

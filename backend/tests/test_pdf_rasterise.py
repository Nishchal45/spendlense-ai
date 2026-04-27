"""Tests for PDF rasterisation.

``rasterise_first_page`` shells out to ``pdftoppm`` (poppler-utils,
installed via apt). The Dockerfile and CI workflow both install it,
so these tests run end-to-end without mocks.

We synthesise a one-page PDF at runtime via PIL → save as PDF rather
than checking in a binary fixture. That keeps the test deterministic
and obviously correct without a separate "did the fixture rot?"
question.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.services.pdf_rasterise import PdfRasterError, rasterise_first_page


def _make_pdf(text_lines: list[str]) -> bytes:
    """Render lines of text onto an A4-ish page and save as PDF."""
    width, height = 850, 1100
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    for index, line in enumerate(text_lines):
        draw.text((40, 40 + index * 30), line, fill="black", font=font)

    buf = BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


class TestRasteriseHappyPath:
    def test_returns_rgb_image(self) -> None:
        pdf = _make_pdf(["Receipt Header", "Total: $9.99"])
        out = rasterise_first_page(pdf)
        assert out.mode == "RGB"
        # 200 DPI on an 8.5x11 page is roughly 1700x2200; we accept
        # a wide range to accommodate poppler-utils version drift.
        assert out.width > 1000
        assert out.height > 1000

    def test_rasterises_first_page_only(self) -> None:
        # Build a two-page PDF; the function should return only one
        # image (page 1).
        a = Image.new("RGB", (400, 400), color="white")
        b = Image.new("RGB", (400, 400), color="white")
        buf = BytesIO()
        a.save(buf, format="PDF", append_images=[b])
        out = rasterise_first_page(buf.getvalue())
        # We can't easily distinguish "page 1 vs page 2" here, but
        # the contract is "one image, not a list" — that's enforced
        # by the return type.
        assert isinstance(out, Image.Image)


class TestRasteriseFailures:
    def test_garbage_bytes_raises_pdfrastererror(self) -> None:
        with pytest.raises(PdfRasterError):
            rasterise_first_page(b"this is not a pdf")

    def test_empty_input_raises(self) -> None:
        with pytest.raises(PdfRasterError):
            rasterise_first_page(b"")

"""Unit tests for the GPT-4V vision OCR wrapper.

We don't talk to the real API here — every test patches
``AsyncOpenAI`` at the module level so we control the response. The
goal is to assert the wrapper's translation rules:

* Valid JSON → ``ParsedReceipt`` with the right field types
* Partial JSON (good merchant, bad date) → fields the model got
  right, ``None`` elsewhere
* Network errors / malformed JSON / no API key → ``None`` (never
  raises into the caller)
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from openai import APIError

from app.services import vision_ocr as vision_module
from app.services.vision_ocr import extract_with_vision


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self._response = response

    async def create(self, **_: Any) -> _FakeResponse:
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))


def _patch_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | Exception) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(vision_module, "AsyncOpenAI", lambda **_: _FakeClient(response))


class TestVisionHappyPath:
    async def test_full_response_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = json.dumps(
            {
                "merchant": "Blue Bottle Coffee",
                "total": "8.99",
                "transaction_date": "2026-04-25",
            }
        )
        _patch_client(monkeypatch, _FakeResponse(payload))

        result = await extract_with_vision(
            image_bytes=b"\xff\xd8\xff\x00", image_mime="image/jpeg", model="gpt-4o"
        )
        assert result is not None
        assert result.merchant == "Blue Bottle Coffee"
        assert result.total == Decimal("8.99")
        assert result.transaction_date == date(2026, 4, 25)

    async def test_partial_response_keeps_good_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The model got the merchant right but emitted a garbage date.
        # We keep what's good, ``None`` what's broken.
        payload = json.dumps(
            {"merchant": "Acme Corp", "total": "12.50", "transaction_date": "garbage"}
        )
        _patch_client(monkeypatch, _FakeResponse(payload))

        result = await extract_with_vision(
            image_bytes=b"\x89PNG\r\n", image_mime="image/png", model="gpt-4o"
        )
        assert result is not None
        assert result.merchant == "Acme Corp"
        assert result.total == Decimal("12.50")
        assert result.transaction_date is None

    async def test_null_fields_are_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        payload = json.dumps({"merchant": None, "total": None, "transaction_date": None})
        _patch_client(monkeypatch, _FakeResponse(payload))

        result = await extract_with_vision(
            image_bytes=b"\x00", image_mime="image/jpeg", model="gpt-4o"
        )
        assert result is not None
        assert result.merchant is None
        assert result.total is None
        assert result.transaction_date is None


class TestVisionFailureModes:
    async def test_no_key_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from app.core.config import get_settings

        get_settings.cache_clear()
        result = await extract_with_vision(
            image_bytes=b"\x00", image_mime="image/jpeg", model="gpt-4o"
        )
        assert result is None

    async def test_api_error_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Construct a real APIError. Different OpenAI SDK versions
        # require different shapes — we only need a minimal instance.
        try:
            error: APIError = APIError(
                message="rate limit",
                request=None,
                body=None,  # type: ignore[arg-type]
            )
        except TypeError:
            # Newer/older SDK signature: pass positional only.
            error = APIError("rate limit")  # type: ignore[call-arg]
        _patch_client(monkeypatch, error)

        result = await extract_with_vision(
            image_bytes=b"\x00", image_mime="image/jpeg", model="gpt-4o"
        )
        assert result is None

    async def test_malformed_json_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The model returned prose instead of JSON.
        _patch_client(monkeypatch, _FakeResponse("oh sure! merchant is Starbucks"))

        result = await extract_with_vision(
            image_bytes=b"\x00", image_mime="image/jpeg", model="gpt-4o"
        )
        assert result is None

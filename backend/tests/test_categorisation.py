"""Unit tests for the categorisation priority chain.

We don't talk to the real OpenAI here — every test patches
``categorise_with_llm`` in :mod:`app.services.categorisation` to
control its return value. That keeps the suite fast, offline, and
deterministic; the LLM client itself has its own thin tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category_correction import CategoryCorrection
from app.models.enums import ExpenseCategory
from app.models.user import User
from app.services import categorisation as categorisation_module
from app.services.categorisation import categorise_merchant
from app.services.user_service import create_user

LLMStub = Callable[[str, str], Awaitable[ExpenseCategory | None]]


@pytest_asyncio.fixture
async def user(db_session: AsyncSession) -> AsyncIterator[User]:
    yield await create_user(
        db_session, email=f"cat-{uuid4()}@example.com", password="hunter2hunter2"
    )


def _patch_llm(monkeypatch: pytest.MonkeyPatch, returns: ExpenseCategory | None) -> None:
    """Replace the LLM call so tests run offline."""

    async def _stub(merchant: str, *, model: str) -> ExpenseCategory | None:
        return returns

    monkeypatch.setattr(categorisation_module, "categorise_with_llm", _stub)


class TestNoMerchant:
    async def test_none_returns_other(self, db_session: AsyncSession) -> None:
        result = await categorise_merchant(db_session, user_id=uuid4(), merchant=None)
        assert result == ExpenseCategory.OTHER

    async def test_blank_returns_other(self, db_session: AsyncSession) -> None:
        result = await categorise_merchant(db_session, user_id=uuid4(), merchant="")
        assert result == ExpenseCategory.OTHER


class TestRulePath:
    async def test_starbucks_resolves_via_rule(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # If the rule layer hits, the LLM must not be called. We
        # configure the stub to raise so any LLM invocation fails the
        # test loudly.
        async def _no_llm(merchant: str, *, model: str) -> ExpenseCategory | None:
            raise AssertionError("rule layer should have short-circuited")

        monkeypatch.setattr(categorisation_module, "categorise_with_llm", _no_llm)
        result = await categorise_merchant(db_session, user_id=user.id, merchant="Starbucks #42")
        assert result == ExpenseCategory.FOOD_DINING


class TestCorrectionsPath:
    async def test_user_correction_overrides_rule(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Starbucks would normally be FOOD_DINING via the rule map.
        # If this user has a correction saying "ENTERTAINMENT", the
        # correction wins.
        db_session.add(
            CategoryCorrection(
                user_id=user.id,
                merchant_name="starbucks #42",
                category=ExpenseCategory.ENTERTAINMENT,
            )
        )
        await db_session.flush()

        # LLM stub raises so we know rule + LLM layers were skipped.
        async def _no_llm(merchant: str, *, model: str) -> ExpenseCategory | None:
            raise AssertionError("correction layer should have short-circuited")

        monkeypatch.setattr(categorisation_module, "categorise_with_llm", _no_llm)
        result = await categorise_merchant(db_session, user_id=user.id, merchant="Starbucks #42")
        assert result == ExpenseCategory.ENTERTAINMENT

    async def test_correction_is_per_user(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # User A has a correction; user B doesn't. Same merchant
        # should categorise differently. The correction row carries an
        # FK to users.id, so the second user has to actually exist.
        other_user = await create_user(
            db_session, email=f"other-{uuid4()}@example.com", password="hunter2hunter2"
        )
        db_session.add(
            CategoryCorrection(
                user_id=other_user.id,
                merchant_name="acme corp",
                category=ExpenseCategory.PERSONAL,
            )
        )
        await db_session.flush()

        _patch_llm(monkeypatch, returns=ExpenseCategory.OTHER)

        # ``user`` has no correction; falls through rules → LLM stub.
        # Acme isn't a rule, so we land on the LLM stub's return.
        result = await categorise_merchant(db_session, user_id=user.id, merchant="Acme Corp")
        assert result == ExpenseCategory.OTHER


class TestLLMPath:
    async def test_falls_through_to_llm_when_no_rule(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            _patch_llm(monkeypatch, returns=ExpenseCategory.HOUSING)
            result = await categorise_merchant(db_session, user_id=user.id, merchant="Random Co")
            assert result == ExpenseCategory.HOUSING
        finally:
            get_settings.cache_clear()

    async def test_llm_failure_falls_back_to_other(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:
            _patch_llm(monkeypatch, returns=None)  # LLM "no opinion"
            result = await categorise_merchant(
                db_session, user_id=user.id, merchant="Some Mystery Vendor"
            )
            assert result == ExpenseCategory.OTHER
        finally:
            get_settings.cache_clear()

    async def test_llm_skipped_when_no_key(
        self,
        db_session: AsyncSession,
        user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No key configured → the categoriser must NOT call the LLM
        # stub. We use an exploding stub to prove it.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from app.core.config import get_settings

        get_settings.cache_clear()
        try:

            async def _no_llm(merchant: str, *, model: str) -> ExpenseCategory | None:
                raise AssertionError("LLM must not be called when key is unset")

            monkeypatch.setattr(categorisation_module, "categorise_with_llm", _no_llm)
            result = await categorise_merchant(db_session, user_id=user.id, merchant="Quiet Vendor")
            assert result == ExpenseCategory.OTHER
        finally:
            get_settings.cache_clear()

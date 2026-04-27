"""Integration tests for the polling + retry endpoints.

``GET /receipts/{id}/status`` and ``POST /receipts/{id}/retry`` are
the surface clients use to observe pipeline progress and recover
from a ``failed`` row. These tests don't exercise the actual OCR
machinery — that lives in ``test_process_receipt_task.py`` — they
just lock in the HTTP contract.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ReceiptStatus
from app.models.receipt import Receipt

API = "/api/v1"

JPEG_PIXEL = b"\xff\xd8\xff" + b"jpeg-body" * 4


async def _register_and_token(client: AsyncClient, email: str) -> str:
    await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "hunter2hunter2"},
    )
    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "hunter2hunter2"},
    )
    return str(login.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def token(client: AsyncClient) -> str:
    return await _register_and_token(client, f"status-{uuid4()}@example.com")


async def _upload(client: AsyncClient, token: str) -> str:
    resp = await client.post(
        f"{API}/receipts",
        files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
        headers=_auth(token),
    )
    assert resp.status_code == 201
    return str(resp.json()["id"])


class TestStatusEndpoint:
    async def test_returns_full_state(self, client: AsyncClient, token: str) -> None:
        receipt_id = await _upload(client, token)

        resp = await client.get(f"{API}/receipts/{receipt_id}/status", headers=_auth(token))
        assert resp.status_code == 200
        body = resp.json()
        # Status is the freshest pipeline state — uploaded right after POST
        # because auto-enqueue short-circuits in the test environment.
        assert body["status"] == "uploaded"
        # The polling shape exposes fields beyond the metadata view.
        assert "error_message" in body
        assert "parsed_payload" in body

    async def test_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        receipt_id = await _upload(client, token)
        stranger = await _register_and_token(client, f"other-{uuid4()}@example.com")
        resp = await client.get(f"{API}/receipts/{receipt_id}/status", headers=_auth(stranger))
        assert resp.status_code == 404

    async def test_404_for_missing(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/receipts/{uuid4()}/status", headers=_auth(token))
        assert resp.status_code == 404


class TestRetryEndpoint:
    async def test_retry_resets_failed_row(
        self, client: AsyncClient, token: str, db_session: AsyncSession
    ) -> None:
        # Force the row into FAILED so retry has something to recover.
        receipt_id = await _upload(client, token)
        receipt = (
            await db_session.execute(select(Receipt).where(Receipt.id == UUID(receipt_id)))
        ).scalar_one()
        receipt.status = ReceiptStatus.FAILED
        receipt.error_message = "tesseract crashed"
        await db_session.commit()

        resp = await client.post(f"{API}/receipts/{receipt_id}/retry", headers=_auth(token))
        assert resp.status_code == 202
        # Returns the reset receipt so the client can flip its UI
        # immediately without a follow-up GET.
        body = resp.json()
        assert body["status"] == "uploaded"

        # DB row is genuinely reset — error message cleared.
        await db_session.refresh(receipt)
        assert receipt.status == ReceiptStatus.UPLOADED
        assert receipt.error_message is None

    async def test_409_for_non_failed_status(self, client: AsyncClient, token: str) -> None:
        # A freshly-uploaded row is in ``uploaded``, not ``failed``,
        # so retry is a logic error and we surface that as 409.
        receipt_id = await _upload(client, token)
        resp = await client.post(f"{API}/receipts/{receipt_id}/retry", headers=_auth(token))
        assert resp.status_code == 409

    async def test_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        receipt_id = await _upload(client, token)
        stranger = await _register_and_token(client, f"retry-other-{uuid4()}@example.com")
        resp = await client.post(f"{API}/receipts/{receipt_id}/retry", headers=_auth(stranger))
        assert resp.status_code == 404

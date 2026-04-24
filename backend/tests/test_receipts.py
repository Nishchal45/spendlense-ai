"""Integration tests for /api/v1/receipts.

These exercise the whole upload path: HTTP → FastAPI → service →
MinIO → Postgres. Each test generates its own user so ownership
boundaries show up as real 404s rather than fixture leaks.

MinIO is reachable at the endpoint configured in ``conftest.py``. The
presigned URL test pulls the blob back through a separate httpx
client, which asserts end-to-end that the wire bytes round-trip
through the object store unchanged.
"""

from __future__ import annotations

import contextlib
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage import delete_object, object_exists
from app.models.receipt import Receipt

API = "/api/v1"


# Synthetic payloads carrying the right magic-byte prefixes. The
# service only sniffs the first 32 bytes and object storage is
# content-agnostic, so we don't need valid image data — just bytes
# starting with the correct signature. The trailing filler keeps the
# payloads distinguishable on round-trip assertions.
JPEG_PIXEL = b"\xff\xd8\xff" + b"jpeg-body" * 4
PNG_PIXEL = b"\x89PNG\r\n\x1a\n" + b"png-body" * 4
PDF_STUB = b"%PDF-1.4\n%fake body for magic-byte sniffing\n%%EOF\n"


async def _register_and_token(client: AsyncClient, email: str) -> str:
    await client.post(
        f"{API}/auth/register",
        json={"email": email, "password": "hunter2hunter2"},
    )
    login = await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": "hunter2hunter2"},
    )
    token = login.json()["access_token"]
    assert isinstance(token, str)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _cleanup_key(key: str) -> None:
    with contextlib.suppress(Exception):
        await delete_object(key=key)


@pytest.fixture
async def token(client: AsyncClient) -> str:
    return await _register_and_token(client, f"owner-{uuid4()}@example.com")


class TestUpload:
    async def test_upload_jpeg_returns_201(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["mime_type"] == "image/jpeg"
        assert body["file_size_bytes"] == len(JPEG_PIXEL)
        assert body["status"] == "uploaded"
        # storage_key is intentionally not on the wire — Phase 4 ADR.
        assert "storage_key" not in body

    async def test_upload_png_returns_201(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.png", PNG_PIXEL, "image/png")},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["mime_type"] == "image/png"

    async def test_upload_pdf_returns_201(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.pdf", PDF_STUB, "application/pdf")},
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["mime_type"] == "application/pdf"

    async def test_upload_requires_auth(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
        )
        assert resp.status_code == 401

    async def test_upload_rejects_unknown_magic_bytes(
        self, client: AsyncClient, token: str
    ) -> None:
        # Client *claims* image/jpeg but bytes are plain text. The
        # server must not trust Content-Type.
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("evil.jpg", b"not-an-image-at-all", "image/jpeg")},
            headers=_auth(token),
        )
        assert resp.status_code == 415

    async def test_upload_rejects_empty_body(self, client: AsyncClient, token: str) -> None:
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("empty.jpg", b"", "image/jpeg")},
            headers=_auth(token),
        )
        assert resp.status_code == 415

    async def test_upload_rejects_oversized_body(self, client: AsyncClient, token: str) -> None:
        # 10 MiB + 1 byte, prefixed with a valid JPEG signature so the
        # MIME sniff passes and the size check is what rejects us. We
        # want 413, not 415.
        oversized = b"\xff\xd8\xff" + b"\x00" * (10 * 1024 * 1024 + 1)
        resp = await client.post(
            f"{API}/receipts",
            files={"file": ("big.jpg", oversized, "image/jpeg")},
            headers=_auth(token),
        )
        assert resp.status_code == 413


class TestGet:
    async def test_get_returns_owned(self, client: AsyncClient, token: str) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = created.json()["id"]

        resp = await client.get(f"{API}/receipts/{receipt_id}", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["id"] == receipt_id

    async def test_get_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = created.json()["id"]

        stranger = await _register_and_token(client, f"stranger-{uuid4()}@example.com")
        resp = await client.get(f"{API}/receipts/{receipt_id}", headers=_auth(stranger))
        # 404 not 403 — existence must not leak.
        assert resp.status_code == 404

    async def test_get_404_for_missing(self, client: AsyncClient, token: str) -> None:
        resp = await client.get(f"{API}/receipts/{uuid4()}", headers=_auth(token))
        assert resp.status_code == 404


class TestList:
    async def test_list_returns_owned_only(self, client: AsyncClient, token: str) -> None:
        await client.post(
            f"{API}/receipts",
            files={"file": ("r1.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        await client.post(
            f"{API}/receipts",
            files={"file": ("r2.png", PNG_PIXEL, "image/png")},
            headers=_auth(token),
        )

        stranger = await _register_and_token(client, f"list-stranger-{uuid4()}@example.com")
        await client.post(
            f"{API}/receipts",
            files={"file": ("mine.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(stranger),
        )

        resp = await client.get(f"{API}/receipts", headers=_auth(token))
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 2
        assert {i["mime_type"] for i in items} == {"image/jpeg", "image/png"}


class TestDownloadUrl:
    async def test_signed_url_returns_original_bytes(self, client: AsyncClient, token: str) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = created.json()["id"]

        resp = await client.get(f"{API}/receipts/{receipt_id}/url", headers=_auth(token))
        assert resp.status_code == 200
        url = resp.json()["url"]
        assert isinstance(url, str) and url.startswith("http")

        async with httpx.AsyncClient() as http:
            blob = await http.get(url)
        assert blob.status_code == 200
        assert blob.content == JPEG_PIXEL

    async def test_signed_url_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = created.json()["id"]

        stranger = await _register_and_token(client, f"url-stranger-{uuid4()}@example.com")
        resp = await client.get(f"{API}/receipts/{receipt_id}/url", headers=_auth(stranger))
        assert resp.status_code == 404


class TestDelete:
    async def test_delete_204_and_blob_gone(
        self, client: AsyncClient, token: str, db_session: AsyncSession
    ) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = UUID(created.json()["id"])

        # Capture the storage key from the in-transaction session so we
        # can assert the blob is actually wiped from the bucket after
        # the delete. The key isn't on the wire — Phase 4 ADR.
        key = (
            await db_session.execute(select(Receipt.storage_key).where(Receipt.id == receipt_id))
        ).scalar_one()
        assert await object_exists(key=key) is True

        resp = await client.delete(f"{API}/receipts/{receipt_id}", headers=_auth(token))
        assert resp.status_code == 204

        # Row is gone.
        gone = await client.get(f"{API}/receipts/{receipt_id}", headers=_auth(token))
        assert gone.status_code == 404
        # Blob is gone.
        assert await object_exists(key=key) is False

    async def test_delete_404_for_other_user(self, client: AsyncClient, token: str) -> None:
        created = await client.post(
            f"{API}/receipts",
            files={"file": ("receipt.jpg", JPEG_PIXEL, "image/jpeg")},
            headers=_auth(token),
        )
        receipt_id = created.json()["id"]

        stranger = await _register_and_token(client, f"del-stranger-{uuid4()}@example.com")
        resp = await client.delete(f"{API}/receipts/{receipt_id}", headers=_auth(stranger))
        assert resp.status_code == 404

        # Owner can still see it.
        owner = await client.get(f"{API}/receipts/{receipt_id}", headers=_auth(token))
        assert owner.status_code == 200

"""Integration tests for the S3 storage wrapper.

These hit the running MinIO container via the same client the
application uses — regressions in auth config, addressing style, or
presign semantics surface here rather than in an endpoint test three
layers up.

Tests are isolated by generating a unique key per case, so cleanup is
best-effort (the bucket is ephemeral in CI and a throwaway local
volume anyway).
"""

from __future__ import annotations

import contextlib
from uuid import uuid4

import httpx
import pytest

from app.core.config import get_settings
from app.core.storage import (
    delete_object,
    object_exists,
    presign_get_url,
    put_object,
    s3_client,
)


def _unique_key() -> str:
    # Use a nested path so the test exercises the same multi-segment
    # layout the real object-key scheme produces.
    return f"tests/{uuid4()}/fixture.bin"


async def _cleanup(key: str) -> None:
    with contextlib.suppress(Exception):
        await delete_object(key=key)


class TestRoundTrip:
    async def test_put_then_exists(self) -> None:
        key = _unique_key()
        try:
            await put_object(key=key, body=b"hello-world", mime_type="text/plain")
            assert await object_exists(key=key) is True
        finally:
            await _cleanup(key)

    async def test_delete_removes_object(self) -> None:
        key = _unique_key()
        await put_object(key=key, body=b"payload", mime_type="text/plain")
        assert await object_exists(key=key) is True

        await delete_object(key=key)
        assert await object_exists(key=key) is False

    async def test_delete_is_idempotent(self) -> None:
        # Deleting a key that never existed must not raise — callers
        # rely on this for the "receipt row gone, blob might already
        # be gone" cleanup path.
        key = _unique_key()
        await delete_object(key=key)


class TestExists:
    async def test_missing_key_returns_false(self) -> None:
        assert await object_exists(key=f"definitely/missing/{uuid4()}") is False


class TestPresignedUrl:
    async def test_presigned_get_returns_body(self) -> None:
        key = _unique_key()
        body = b"the-quick-brown-fox"
        try:
            await put_object(key=key, body=body, mime_type="text/plain")
            url = await presign_get_url(key=key, expires_in=60)

            # The signed URL points at the internal ``minio`` hostname
            # (that's how the API container talks to the bucket). Swap
            # it for the docker-network-resolvable one used by the
            # test runner, which lives in the same compose network.
            async with httpx.AsyncClient() as client:
                resp = await client.get(url)
            assert resp.status_code == 200
            assert resp.content == body
        finally:
            await _cleanup(key)

    async def test_presign_contains_expected_key(self) -> None:
        key = _unique_key()
        url = await presign_get_url(key=key, expires_in=60)
        # URL-encoded slashes are fine; just check the terminal segment
        # to confirm we're signing the right object.
        assert "fixture.bin" in url


@pytest.mark.parametrize(
    "mime_type",
    ["image/jpeg", "image/png", "application/pdf"],
)
async def test_put_preserves_content_type(mime_type: str) -> None:
    """MIME round-trips through S3 metadata so presigned GETs serve the
    right ``Content-Type`` header — browsers rely on it for inline
    rendering of images."""
    key = _unique_key()
    try:
        await put_object(key=key, body=b"\x00\x01\x02", mime_type=mime_type)
        async with s3_client() as client:
            head = await client.head_object(Bucket=get_settings().s3_bucket, Key=key)
        assert head["ContentType"] == mime_type
    finally:
        await _cleanup(key)

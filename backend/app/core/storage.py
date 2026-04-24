"""Thin async wrapper around the S3-compatible object store.

The receipt upload flow needs three operations: put a blob, get a
presigned URL for download, and delete. The interface is deliberately
narrow so it can be swapped for a filesystem backend during tests or
self-hosted deploys that don't want to run MinIO.

Why aioboto3 rather than the minio-py client:

* Same API as production boto3 — code transfers 1:1 when the bucket
  moves from MinIO to real S3.
* Native async — no thread-pool shim for concurrent uploads.
* Signed URLs and server-side copy work out of the box.

The client is process-global (lazy, lock-guarded) so we don't open a
fresh session per request — aioboto3 sessions are cheap but the
underlying ``aiobotocore`` client has a non-trivial TLS setup.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from aiobotocore.config import AioConfig

from app.core.config import get_settings

# Presigned GET URLs expire quickly enough that a leaked link has a
# short blast radius but long enough to survive a slow mobile connection.
PRESIGN_GET_TTL_SECONDS = 300  # 5 minutes

_session_lock = asyncio.Lock()
_session: aioboto3.Session | None = None


async def _get_session() -> aioboto3.Session:
    global _session
    # Double-checked lock keeps the fast path allocation-free without
    # racing two concurrent first-callers into two sessions.
    if _session is None:
        async with _session_lock:
            if _session is None:
                _session = aioboto3.Session()
    return _session


@asynccontextmanager
async def s3_client() -> AsyncIterator[Any]:
    """Yield an S3 client bound to the configured endpoint.

    Use inside ``async with`` so the underlying connection pool is
    closed promptly — aiobotocore leaks sockets if the client isn't
    awaited-closed.
    """
    settings = get_settings()
    session = await _get_session()
    # Virtual-hosted-style addressing breaks against MinIO on a bare
    # endpoint (no DNS for ``<bucket>.minio``). Force path-style so the
    # same code path works for both MinIO and real S3 (which accepts
    # path-style too, just slower to phase out).
    config = AioConfig(signature_version="s3v4", s3={"addressing_style": "path"})
    async with session.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=config,
    ) as client:
        yield client


async def put_object(
    *,
    key: str,
    body: bytes,
    mime_type: str,
) -> None:
    """Upload ``body`` to ``key`` with the given ``mime_type``."""
    settings = get_settings()
    async with s3_client() as client:
        await client.put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=mime_type,
        )


async def delete_object(*, key: str) -> None:
    """Delete ``key`` from the bucket. Idempotent — S3 returns 204 even
    when the key is already gone, which is what we want for the
    cascade-on-receipt-delete path."""
    settings = get_settings()
    async with s3_client() as client:
        await client.delete_object(Bucket=settings.s3_bucket, Key=key)


async def presign_get_url(*, key: str, expires_in: int = PRESIGN_GET_TTL_SECONDS) -> str:
    """Return a short-lived signed URL for ``GET`` of ``key``."""
    settings = get_settings()
    async with s3_client() as client:
        url: str = await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url


async def object_exists(*, key: str) -> bool:
    """Return True if ``key`` exists in the bucket.

    Uses a HEAD request so it doesn't pull the body. Swallows 404 and
    403 (MinIO returns 403 on missing keys when the caller lacks
    ``ListBucket`` — we only care about "can I read this", not
    distinguishing the two).
    """
    settings = get_settings()
    async with s3_client() as client:
        try:
            await client.head_object(Bucket=settings.s3_bucket, Key=key)
            return True
        except client.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "403"}:
                return False
            raise

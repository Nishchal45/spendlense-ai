"""Object-key scheme for receipt blobs in S3/MinIO.

Design goals, in priority order:

1. **No user id in the key.** A bucket listing or a CDN log must not
   reveal which user owns an object. We derive an opaque per-user
   prefix by HMAC-ing the user id with the JWT secret; attackers who
   see keys can't walk back to the user without the secret.
2. **Deterministic per user.** The same user always lands under the
   same prefix so operational tasks (e.g. "delete everything owned by
   user X on account deletion") don't need a DB scan — just a prefix
   list. We don't rely on the prefix for authorisation; every access
   checks ownership via the ``receipts`` table.
3. **Month-sharded.** ``<prefix>/<yyyy>/<mm>/`` keeps a single user's
   uploads from all landing in a flat virtual directory, which slows
   down list operations on S3-compatible backends past ~100k objects.
4. **Collision-proof.** The last segment is a fresh UUIDv4 so two
   requests in the same millisecond can't overwrite each other.
5. **Extension preserved.** We append the inferred extension for
   debuggability and so browsers do the right thing if a signed URL
   ever points at the raw object.

Example key::

    receipts/b83f6f2a8d9c1e7a/2026/04/3e77d4c8-9d1c-4e89-b2ec-2b1a0a7a1f1e.jpg
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from uuid import UUID, uuid4

# HMAC-SHA256 truncated to 16 hex chars = 64 bits. That's 2^32 users
# before a 50% collision probability (birthday bound) — fine for a
# single-tenant self-hosted app and short enough to keep keys readable.
_USER_PREFIX_LEN = 16
_MIME_TO_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heif",
    "application/pdf": "pdf",
}


def _user_prefix(user_id: UUID, secret: str) -> str:
    """Opaque, stable prefix derived from ``user_id`` under ``secret``.

    Using ``hmac`` (not a plain hash) means an attacker who sees the
    prefix can't brute-force user ids — UUIDv4 has 122 bits of entropy,
    which is plenty, but HMAC makes the intent explicit and lets us
    rotate the derivation by bumping the secret.
    """
    mac = hmac.new(secret.encode("utf-8"), str(user_id).encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:_USER_PREFIX_LEN]


def extension_for(mime_type: str) -> str | None:
    """Return the preferred file extension for ``mime_type`` or ``None``.

    Unknown types return ``None`` so the caller can reject them — we
    don't want an opaque ``.bin`` file landing in the bucket.
    """
    return _MIME_TO_EXT.get(mime_type.lower().strip())


def build_receipt_key(
    *,
    user_id: UUID,
    mime_type: str,
    secret: str,
    now: datetime | None = None,
    object_id: UUID | None = None,
) -> str:
    """Return the storage key under which a receipt should be written.

    ``now`` and ``object_id`` are injected for testability; production
    callers leave them as defaults (wall clock + fresh UUID).

    Raises :class:`ValueError` if ``mime_type`` isn't on the allowlist.
    The upload endpoint validates MIME before calling this, so hitting
    the raise here indicates a bug rather than user input.
    """
    extension = extension_for(mime_type)
    if extension is None:
        raise ValueError(f"unsupported mime_type for storage key: {mime_type!r}")

    now = now or datetime.now(UTC)
    object_id = object_id or uuid4()
    prefix = _user_prefix(user_id, secret)
    return f"receipts/{prefix}/{now.year:04d}/{now.month:02d}/{object_id}.{extension}"

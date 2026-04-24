"""Unit tests for the receipt object-key scheme."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.storage_keys import build_receipt_key, extension_for

_SECRET = "test-secret-must-be-at-least-32-characters-long"
_FIXED_TIME = datetime(2026, 4, 21, 12, 0, tzinfo=UTC)
_FIXED_OBJECT_ID = UUID("3e77d4c8-9d1c-4e89-b2ec-2b1a0a7a1f1e")


def test_extension_for_known_types() -> None:
    assert extension_for("image/jpeg") == "jpg"
    assert extension_for("image/JPEG") == "jpg"
    assert extension_for("image/png") == "png"
    assert extension_for("image/webp") == "webp"
    assert extension_for("image/heic") == "heic"
    assert extension_for("application/pdf") == "pdf"


def test_extension_for_unknown_types_returns_none() -> None:
    assert extension_for("application/octet-stream") is None
    assert extension_for("image/gif") is None  # deliberately unsupported
    assert extension_for("") is None


def test_key_shape_matches_expected_pattern() -> None:
    user_id = UUID("a1520451-7559-4558-97b5-1661acac4740")
    key = build_receipt_key(
        user_id=user_id,
        mime_type="image/jpeg",
        secret=_SECRET,
        now=_FIXED_TIME,
        object_id=_FIXED_OBJECT_ID,
    )
    # receipts/<16-hex>/<year>/<month>/<uuid>.<ext>
    assert re.fullmatch(
        r"receipts/[0-9a-f]{16}/2026/04/"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.jpg",
        key,
    )


def test_key_does_not_leak_user_id() -> None:
    user_id = UUID("a1520451-7559-4558-97b5-1661acac4740")
    key = build_receipt_key(user_id=user_id, mime_type="image/png", secret=_SECRET, now=_FIXED_TIME)
    assert str(user_id) not in key
    # The bare hex of the UUID shouldn't appear either — HMAC prefix is
    # derived, not a substring.
    assert user_id.hex not in key


def test_key_is_deterministic_prefix_per_user() -> None:
    user_id = uuid4()
    k1 = build_receipt_key(user_id=user_id, mime_type="image/jpeg", secret=_SECRET, now=_FIXED_TIME)
    k2 = build_receipt_key(user_id=user_id, mime_type="image/png", secret=_SECRET, now=_FIXED_TIME)
    # Same user, same month → same prefix (different extension + object id).
    assert k1.split("/")[1] == k2.split("/")[1]


def test_key_prefix_differs_across_users() -> None:
    u1 = uuid4()
    u2 = uuid4()
    k1 = build_receipt_key(user_id=u1, mime_type="image/jpeg", secret=_SECRET, now=_FIXED_TIME)
    k2 = build_receipt_key(user_id=u2, mime_type="image/jpeg", secret=_SECRET, now=_FIXED_TIME)
    assert k1.split("/")[1] != k2.split("/")[1]


def test_key_prefix_changes_with_secret() -> None:
    # Rotating the secret rotates the derivation — same user gets a new
    # prefix. This is the intended "revoke old keys" behaviour.
    user_id = uuid4()
    k1 = build_receipt_key(
        user_id=user_id, mime_type="image/jpeg", secret="secret-one" * 4, now=_FIXED_TIME
    )
    k2 = build_receipt_key(
        user_id=user_id, mime_type="image/jpeg", secret="secret-two" * 4, now=_FIXED_TIME
    )
    assert k1.split("/")[1] != k2.split("/")[1]


def test_key_rejects_unsupported_mime_type() -> None:
    with pytest.raises(ValueError, match="unsupported mime_type"):
        build_receipt_key(
            user_id=uuid4(),
            mime_type="application/x-executable",
            secret=_SECRET,
        )


def test_key_uses_utc_month_shard() -> None:
    # Ensure the year/month segment tracks UTC, not local time, so
    # shards line up regardless of where the container runs.
    user_id = uuid4()
    jan = datetime(2026, 1, 15, tzinfo=UTC)
    feb = datetime(2026, 2, 1, tzinfo=UTC)
    kj = build_receipt_key(user_id=user_id, mime_type="image/jpeg", secret=_SECRET, now=jan)
    kf = build_receipt_key(user_id=user_id, mime_type="image/jpeg", secret=_SECRET, now=feb)
    assert "/2026/01/" in kj
    assert "/2026/02/" in kf

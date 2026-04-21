"""Unit tests for :mod:`app.core.security`.

These run without a database or an HTTP client — pure crypto primitives,
exercised the way the auth endpoints will use them.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_is_not_plaintext(self) -> None:
        hashed = hash_password("correct horse battery staple")
        assert hashed != "correct horse battery staple"
        assert hashed.startswith("$2b$")  # bcrypt identifier

    def test_verify_accepts_matching_password(self) -> None:
        hashed = hash_password("s3cret!")
        assert verify_password("s3cret!", hashed) is True

    def test_verify_rejects_wrong_password(self) -> None:
        hashed = hash_password("s3cret!")
        assert verify_password("wrong", hashed) is False

    def test_same_password_produces_different_hashes(self) -> None:
        # bcrypt generates a fresh salt every call; two hashes of the same
        # plaintext must differ so a stolen DB can't be rainbow-tabled.
        assert hash_password("same") != hash_password("same")


class TestAccessTokens:
    def test_token_round_trip(self) -> None:
        user_id = uuid4()
        token = create_access_token(user_id)
        claims = decode_access_token(token)
        assert claims["sub"] == str(user_id)
        assert claims["type"] == "access"
        assert claims["exp"] > claims["iat"]

    def test_extra_claims_are_preserved(self) -> None:
        token = create_access_token("user-42", extra_claims={"scope": "admin"})
        claims = decode_access_token(token)
        assert claims["scope"] == "admin"

    def test_expired_token_is_rejected(self) -> None:
        token = create_access_token("user", expires_in=timedelta(seconds=-1))
        with pytest.raises(TokenError):
            decode_access_token(token)

    def test_tampered_token_is_rejected(self) -> None:
        token = create_access_token("user")
        tampered = token[:-4] + "AAAA"
        with pytest.raises(TokenError):
            decode_access_token(tampered)

    def test_wrong_token_type_is_rejected(self) -> None:
        # If a future refresh-token flow accidentally reuses the decoder,
        # the ``type`` check stops it from authenticating a session.
        from jose import jwt

        from app.core.config import get_settings

        settings = get_settings()
        bad = jwt.encode(
            {"sub": "user", "type": "refresh", "iat": 0, "exp": 99999999999},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(TokenError, match="unexpected token type"):
            decode_access_token(bad)

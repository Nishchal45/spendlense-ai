"""Unit tests for the at-rest secret box.

These exercise the encrypt/decrypt round trip plus every documented
failure path. Tests pin the key via ``monkeypatch`` rather than
relying on conftest setup so each case is self-contained.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.secret_box import (
    SecretBoxError,
    SecretBoxNotConfiguredError,
    decrypt_secret,
    encrypt_secret,
)


@pytest.fixture
def fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Mint a fresh key per test and pin it on the settings cache."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key)
    from app.core.config import get_settings

    get_settings.cache_clear()
    return key


class TestRoundTrip:
    def test_encrypt_then_decrypt(self, fernet_key: str) -> None:
        ciphertext = encrypt_secret("very-secret-refresh-token")
        # Ciphertext must NOT contain the plaintext anywhere — the
        # whole point. (Fernet's ciphertext is base64'd random
        # bytes, so this is really an "is encryption running?"
        # smoke test.)
        assert "very-secret-refresh-token" not in ciphertext
        assert decrypt_secret(ciphertext) == "very-secret-refresh-token"

    def test_two_encrypts_produce_different_ciphertexts(self, fernet_key: str) -> None:
        # Fernet randomises the IV per call; identical plaintexts
        # produce different ciphertexts. If they don't, the key
        # is being misused.
        a = encrypt_secret("same-input")
        b = encrypt_secret("same-input")
        assert a != b
        assert decrypt_secret(a) == decrypt_secret(b) == "same-input"


class TestFailureModes:
    def test_decrypt_with_wrong_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Encrypt with key A, swap to key B, decrypt — must fail
        # cleanly, not return garbage plaintext.
        from app.core.config import get_settings

        key_a = Fernet.generate_key().decode("ascii")
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key_a)
        get_settings.cache_clear()
        ciphertext = encrypt_secret("payload")

        key_b = Fernet.generate_key().decode("ascii")
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", key_b)
        get_settings.cache_clear()

        with pytest.raises(SecretBoxError, match="authentication"):
            decrypt_secret(ciphertext)

    def test_decrypt_tampered_ciphertext_raises(self, fernet_key: str) -> None:
        ciphertext = encrypt_secret("payload")
        # Flip a bit in the middle — Fernet's HMAC catches it.
        tampered = ciphertext[: len(ciphertext) // 2] + "X" + ciphertext[len(ciphertext) // 2 + 1 :]
        with pytest.raises(SecretBoxError):
            decrypt_secret(tampered)

    def test_encrypt_without_key_raises_typed_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GMAIL_TOKEN_ENCRYPTION_KEY", raising=False)
        from app.core.config import get_settings

        get_settings.cache_clear()
        # Specific subclass — the OAuth route maps this to a 503,
        # not a generic 500.
        with pytest.raises(SecretBoxNotConfiguredError):
            encrypt_secret("anything")

    def test_malformed_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GMAIL_TOKEN_ENCRYPTION_KEY", "not-a-valid-fernet-key")
        from app.core.config import get_settings

        get_settings.cache_clear()
        with pytest.raises(SecretBoxError, match="malformed"):
            encrypt_secret("anything")

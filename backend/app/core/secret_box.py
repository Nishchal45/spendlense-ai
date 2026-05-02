"""Symmetric encryption for at-rest secrets.

Used by Phase 5.6 to keep Gmail refresh tokens encrypted in the
database. The threat model: a database backup or a compromised SQL
read should not expose long-lived refresh tokens that grant access
to the user's mailbox. Encryption at rest closes that channel —
the tokens are unreadable without ``GMAIL_TOKEN_ENCRYPTION_KEY``,
which lives in the API process's environment, not the DB.

We use **Fernet** (``cryptography.fernet``) — AES-128-CBC + HMAC-
SHA256, IND-CCA2 secure with a versioned ciphertext format. Why
Fernet specifically:

* **Authenticated.** Tampering with the ciphertext fails on
  decrypt — we don't have to re-implement HMAC-then-decrypt.
* **Versioned.** ``Fernet`` ciphertexts carry a version byte, so
  a future migration to ChaCha20-Poly1305 is a parser change.
* **Battle-tested.** ``cryptography`` is the canonical Python
  crypto lib; rolling our own AES would be reinvention.

Key format: 32-byte URL-safe base64 (Fernet's standard). Generate
with ``Fernet.generate_key()`` once; store in ``.env`` and rotate
manually with a re-encrypt migration when needed.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class SecretBoxError(Exception):
    """Raised on any encrypt/decrypt failure path.

    Wraps :class:`cryptography.fernet.InvalidToken` so callers don't
    have to import the underlying lib to write ``except`` arms — and
    so a single barrier exception lets us rotate the underlying
    primitive without touching every call site.
    """


class SecretBoxNotConfiguredError(SecretBoxError):
    """``GMAIL_TOKEN_ENCRYPTION_KEY`` is unset.

    Surfaced as its own type so the OAuth route can return a
    targeted 503 ("the integration is not configured") rather than
    a generic encryption failure.
    """


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` and return the URL-safe ciphertext."""
    box = _load_box()
    return box.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt ``ciphertext`` and return the original plaintext.

    Raises :class:`SecretBoxError` on tampered or wrong-key input.
    Callers map that to a 503 / re-auth flow — there's no recovery
    path, the ciphertext is opaque garbage to us at that point.
    """
    box = _load_box()
    try:
        return box.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretBoxError("ciphertext failed authentication") from exc


def _load_box() -> Fernet:
    """Construct the Fernet instance from ``settings.gmail_token_encryption_key``.

    The key never lives in module state — we re-construct ``Fernet``
    on every call so a key rotation in the env (followed by a
    process restart) takes effect immediately. The cost is one
    base64-decode per call, which is negligible compared to the DB
    round trip these calls accompany.
    """
    settings = get_settings()
    key = settings.gmail_token_encryption_key
    if not key:
        raise SecretBoxNotConfiguredError(
            "GMAIL_TOKEN_ENCRYPTION_KEY is not set; cannot encrypt or decrypt secrets"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        # ``Fernet`` raises ``ValueError`` on a malformed key (wrong
        # length / wrong base64). Wrapping into the module's barrier
        # exception keeps callers from importing ``cryptography``.
        raise SecretBoxError(f"GMAIL_TOKEN_ENCRYPTION_KEY is malformed: {exc}") from exc

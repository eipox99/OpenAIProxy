from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from openproxy.config import settings


def _derive_key(raw_key: str) -> bytes:
    """Derive a valid 32-byte Fernet key from an arbitrary string using SHA-256."""
    if not raw_key:
        msg = "ENCRYPTION_KEY is not set. Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        raise RuntimeError(msg)
    hash_bytes = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(hash_bytes)


def _get_fernet() -> Fernet:
    return Fernet(_derive_key(settings.encryption_key))


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key. Returns a base64-encoded ciphertext string."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an API key previously encrypted with encrypt_api_key."""
    if not ciphertext:
        return ""
    return _get_fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")

"""Encryption for secrets at rest (spec 8, 21.4). Plaintext secrets never leave the server and are
never logged; only a short hint (last 4 characters) is ever shown."""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


@lru_cache
def _fernet() -> MultiFernet:
    s = get_settings()
    if s.encryption_keys:
        keys = [k.strip() for k in s.encryption_keys.split(",") if k.strip()]
    else:
        digest = hashlib.sha256(f"invoiceflow-secrets:{s.secret_key}".encode()).digest()
        keys = [base64.urlsafe_b64encode(digest).decode()]
    return MultiFernet([Fernet(k) for k in keys])


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Secret could not be decrypted (wrong or rotated key)") from exc


def secret_hint(plaintext: str) -> str:
    return f"…{plaintext[-4:]}" if len(plaintext) >= 8 else "…"


def generate_key() -> str:
    return Fernet.generate_key().decode()

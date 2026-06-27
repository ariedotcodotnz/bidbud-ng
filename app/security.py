"""Symmetric encryption for the stored TradeMe password.

The Fernet key comes from ``BIDBUD_SECRET_KEY`` if set, otherwise a key is
generated once and persisted to ``data/secret.key`` (gitignored).
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from . import config


def _load_key() -> bytes:
    env = os.environ.get("BIDBUD_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    if config.SECRET_KEY_PATH.exists():
        return config.SECRET_KEY_PATH.read_bytes()
    key = Fernet.generate_key()
    config.SECRET_KEY_PATH.write_bytes(key)
    try:
        os.chmod(config.SECRET_KEY_PATH, 0o600)
    except OSError:
        pass
    return key


_FERNET = Fernet(_load_key())


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _FERNET.encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _FERNET.decrypt(token.encode()).decode()
    except InvalidToken:
        return ""

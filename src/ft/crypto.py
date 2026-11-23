"""Encrypt tokens at rest with a key held in .env (Fernet = AES + integrity check)."""
import os
from cryptography.fernet import Fernet


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = os.getenv("FT_ENCRYPTION_KEY")
    if not key:
        raise SystemExit("FT_ENCRYPTION_KEY is empty. Run: python -m ft.cli genkey, then paste it into .env")
    return Fernet(key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()

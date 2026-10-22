"""Symmetric encryption for user API keys (Fernet).

Generate a key once:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Store it as MASTER_ENCRYPTION_KEY in .env, Streamlit secrets and GitHub Actions secrets.
(ENCRYPTION_KEY is also accepted for older setups.) The app and the worker MUST use the same key.
"""
import os

from cryptography.fernet import Fernet, InvalidToken


def _find_key() -> str | None:
    for name in ("MASTER_ENCRYPTION_KEY", "ENCRYPTION_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    try:
        import streamlit as st
        for name in ("MASTER_ENCRYPTION_KEY", "ENCRYPTION_KEY"):
            if name in st.secrets:
                return st.secrets[name]
    except Exception:
        pass
    return None


def _fernet() -> Fernet:
    key = _find_key()
    if not key:
        raise RuntimeError("MASTER_ENCRYPTION_KEY is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_key(plain: str | None) -> str | None:
    if not plain:
        return None
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_key(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None

import os
import secrets

from cryptography.fernet import Fernet, InvalidToken
from pwdlib import PasswordHash


password_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_hasher.verify(password, password_hash)


def _fernet() -> Fernet:
    key = os.getenv("SETTINGS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("SETTINGS_ENCRYPTION_KEY is not configured")
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("无法读取加密配置；请确认 SETTINGS_ENCRYPTION_KEY 未被更换") from exc


def session_secret() -> str:
    return os.getenv("SESSION_SECRET", "development-only-change-this-secret")


def csrf_token(session: dict) -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def validate_csrf(session: dict, token: str) -> bool:
    return bool(token) and secrets.compare_digest(session.get("csrf_token", ""), token)

"""Authentication utilities: Argon2 hashing, JWT, session management."""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from server.config import get_settings

_ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,   # 64 MB
    parallelism=2,
    hash_len=32,
    salt_len=16,
)


# ── Password ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


# ── JWT tokens ───────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, session_id: str) -> tuple[str, datetime]:
    cfg = get_settings()
    exp = _now() + timedelta(minutes=cfg.access_token_ttl)
    payload = {
        "sub":  user_id,
        "sid":  session_id,
        "type": "access",
        "iat":  _now(),
        "exp":  exp,
    }
    token = jwt.encode(payload, cfg.jwt_secret, algorithm="HS256")
    return token, exp


def create_refresh_token(user_id: str, session_id: str) -> tuple[str, datetime]:
    cfg = get_settings()
    exp = _now() + timedelta(days=cfg.refresh_token_ttl)
    payload = {
        "sub":  user_id,
        "sid":  session_id,
        "type": "refresh",
        "iat":  _now(),
        "exp":  exp,
    }
    token = jwt.encode(payload, cfg.jwt_secret, algorithm="HS256")
    return token, exp


def decode_token(token: str, expected_type: str = "access") -> dict:
    """Raises jwt.PyJWTError on failure."""
    cfg = get_settings()
    payload = jwt.decode(token, cfg.jwt_secret, algorithms=["HS256"])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"Expected {expected_type} token")
    return payload


def hash_token(token: str) -> str:
    """Store only the hash of refresh tokens in the DB (like password hashing for tokens)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_id() -> str:
    import uuid
    return str(uuid.uuid4())  # 36 chars, compatível com VARCHAR(36)


# ── Rate limiting helpers ─────────────────────────────────────────────────────

def is_locked(locked_until: Optional[datetime]) -> bool:
    if locked_until is None:
        return False
    return _now() < locked_until


def lockout_until() -> datetime:
    return _now() + timedelta(minutes=get_settings().lockout_minutes)

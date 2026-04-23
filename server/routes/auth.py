"""Authentication routes: register, login, refresh, logout, MFA."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import pyotp
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server import auth as auth_utils
from server.database import get_db
from server.models import Session, User
from server.schemas import (
    LoginRequest, MfaSetupResponse, MfaVerifyRequest,
    RefreshRequest, RegisterRequest, TokenResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Shared dependency (defined first to avoid forward-reference) ─────────────

async def _get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = auth_utils.decode_token(creds.credentials, expected_type="access")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


get_current_user = _get_current_user


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    exists = await db.scalar(select(User).where(
        (User.username == req.username.lower()) | (User.email == req.email.lower())
    ))
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already taken")

    user = User(
        username=req.username.lower(),
        email=req.email.lower(),
        password_hash=auth_utils.hash_password(req.password),
        pub_key_exchange=req.pub_key_exchange,
        pub_key_sign=req.pub_key_sign,
    )
    db.add(user)
    await db.flush()

    access_tok, refresh_tok, _ = await _create_session(user, db, device_name="default")
    await db.commit()

    logger.info("register username=%s", user.username)
    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        expires_in=auth_utils.get_settings().access_token_ttl * 60,
    )


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.username == req.username.lower()))
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if auth_utils.is_locked(user.locked_until):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Account temporarily locked")

    if not auth_utils.verify_password(req.password, user.password_hash):
        user.failed_logins += 1
        cfg = auth_utils.get_settings()
        if user.failed_logins >= cfg.max_login_attempts:
            user.locked_until = auth_utils.lockout_until()
            user.failed_logins = 0
            await db.commit()
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts — locked")
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    if user.mfa_enabled:
        if not req.totp_code:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA code required")
        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(req.totp_code, valid_window=1):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA code")

    if auth_utils.needs_rehash(user.password_hash):
        user.password_hash = auth_utils.hash_password(req.password)

    user.failed_logins = 0
    user.locked_until  = None
    user.last_seen     = _now()

    access_tok, refresh_tok, _ = await _create_session(user, db, device_name=req.device_name)
    await db.commit()

    logger.info("login username=%s", user.username)
    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        expires_in=auth_utils.get_settings().access_token_ttl * 60,
    )


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = auth_utils.decode_token(req.refresh_token, expected_type="refresh")
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")

    token_hash = auth_utils.hash_token(req.refresh_token)
    session = await db.scalar(select(Session).where(
        Session.token_hash == token_hash,
        Session.is_revoked == False,  # noqa: E712
    ))
    if not session or session.expires_at.replace(tzinfo=timezone.utc) < _now():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired or revoked")

    user = await db.get(User, payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")

    session.is_revoked = True
    access_tok, refresh_tok, _ = await _create_session(user, db, device_name=session.device_name)
    await db.commit()

    return TokenResponse(
        access_token=access_tok,
        refresh_token=refresh_tok,
        expires_in=auth_utils.get_settings().access_token_ttl * 60,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=204)
async def logout(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = auth_utils.hash_token(req.refresh_token)
    session = await db.scalar(select(Session).where(Session.token_hash == token_hash))
    if session:
        session.is_revoked = True
        await db.commit()


# ── MFA ───────────────────────────────────────────────────────────────────────

@router.post("/mfa/setup", response_model=MfaSetupResponse)
async def mfa_setup(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    import secrets as _secrets
    secret = pyotp.random_base32()
    totp   = pyotp.TOTP(secret)
    uri    = totp.provisioning_uri(current_user.email, issuer_name="msgTUI")
    current_user.mfa_secret = secret
    await db.commit()
    return MfaSetupResponse(
        secret=secret,
        qr_uri=uri,
        backup_codes=[_secrets.token_hex(8) for _ in range(8)],
    )


@router.post("/mfa/enable", status_code=204)
async def mfa_enable(
    req: MfaVerifyRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.mfa_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Call /mfa/setup first")
    totp = pyotp.TOTP(current_user.mfa_secret)
    if not totp.verify(req.totp_code, valid_window=1):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code")
    current_user.mfa_enabled = True
    await db.commit()


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _create_session(user: User, db: AsyncSession, device_name=None):
    sid        = auth_utils.generate_session_id()
    access_tok, _  = auth_utils.create_access_token(user.id, sid)
    refresh_tok, exp = auth_utils.create_refresh_token(user.id, sid)
    session = Session(
        id=sid,
        user_id=user.id,
        token_hash=auth_utils.hash_token(refresh_tok),
        device_name=device_name,
        expires_at=exp,
    )
    db.add(session)
    return access_tok, refresh_tok, session

"""Pydantic request/response schemas for the REST API."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_validator
import re


# ── Auth ────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username:         str   = Field(min_length=3, max_length=64)
    email:            EmailStr
    password:         str   = Field(min_length=12, max_length=128)
    pub_key_exchange: str   # base64 X25519 public key
    pub_key_sign:     str   # base64 Ed25519 public key

    @field_validator("username")
    @classmethod
    def username_safe(cls, v: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_.\-]+", v):
            raise ValueError("username may only contain letters, digits, _, ., -")
        return v.lower()

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if not any(c.isupper() for c in v):
            raise ValueError("password must contain at least one uppercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("password must contain at least one digit")
        if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in v):
            raise ValueError("password must contain at least one special character")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str
    device_name: Optional[str] = None
    totp_code:   Optional[str] = None


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int


class RefreshRequest(BaseModel):
    refresh_token: str


class MfaSetupResponse(BaseModel):
    secret:       str
    qr_uri:       str
    backup_codes: list[str]


class MfaVerifyRequest(BaseModel):
    totp_code: str = Field(min_length=6, max_length=6)


# ── Users ───────────────────────────────────────────────────────────────────

class UserPublic(BaseModel):
    id:               str
    username:         str
    pub_key_exchange: Optional[str] = None
    pub_key_sign:     Optional[str] = None
    last_seen:        Optional[datetime] = None
    mfa_enabled:      bool = False

    model_config = {"from_attributes": True}


class UserMe(UserPublic):
    email:      str
    created_at: datetime
    mfa_enabled: bool

    model_config = {"from_attributes": True}


class KeyBundle(BaseModel):
    user_id:          str
    pub_key_exchange: str
    pub_key_sign:     str


# ── Conversations ───────────────────────────────────────────────────────────

class ConversationOut(BaseModel):
    id:         str
    peer:       UserPublic
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Messages ────────────────────────────────────────────────────────────────

class MessageOut(BaseModel):
    id:              str
    conversation_id: Optional[str] = None
    group_id:        Optional[str] = None
    sender_id:       str
    ciphertext:      str
    nonce:           str
    ephemeral_pub:   str
    signature:       str
    timestamp:       float
    delivered:       bool
    read:            bool

    model_config = {"from_attributes": True}


class MessageHistoryRequest(BaseModel):
    before_timestamp: Optional[float] = None
    limit:            int = Field(default=50, le=200, ge=1)


# ── Groups ──────────────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name:        str = Field(min_length=1, max_length=128)
    member_ids:  list[str] = Field(default_factory=list)
    key_bundle:  Optional[str] = None  # JSON: {user_id: encrypted_group_key}


class GroupOut(BaseModel):
    id:         str
    name:       str
    creator_id: str
    created_at: datetime
    members:    list[UserPublic] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class GroupMemberAdd(BaseModel):
    user_id:           str
    encrypted_key_for: Optional[str] = None  # New key slice for the new member


class GroupKeyBundle(BaseModel):
    key_bundle: str  # Updated full JSON bundle

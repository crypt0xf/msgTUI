"""SQLAlchemy ORM models."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index,
    Integer, String, Text, Float,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


# ── Users ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id:             Mapped[str]  = mapped_column(String(36), primary_key=True, default=_uid)
    username:       Mapped[str]  = mapped_column(String(64), unique=True, nullable=False, index=True)
    email:          Mapped[str]  = mapped_column(String(256), unique=True, nullable=False, index=True)
    password_hash:  Mapped[str]  = mapped_column(Text, nullable=False)

    # X25519 public key (base64) for ECDH key exchange
    pub_key_exchange: Mapped[str | None] = mapped_column(Text)
    # Ed25519 public key (base64) for message signing / verification
    pub_key_sign:     Mapped[str | None] = mapped_column(Text)

    mfa_secret:     Mapped[str | None]  = mapped_column(String(64))  # TOTP secret (encrypted)
    mfa_enabled:    Mapped[bool]        = mapped_column(Boolean, default=False)

    created_at:     Mapped[datetime]    = mapped_column(DateTime(timezone=True), default=_now)
    last_seen:      Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active:      Mapped[bool]        = mapped_column(Boolean, default=True)

    # Brute-force protection
    failed_logins:  Mapped[int]         = mapped_column(Integer, default=0)
    locked_until:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions:       Mapped[list["Session"]]     = relationship(back_populates="user", cascade="all, delete-orphan")
    sent_messages:  Mapped[list["Message"]]     = relationship(back_populates="sender", cascade="all, delete-orphan")
    group_memberships: Mapped[list["GroupMember"]] = relationship(back_populates="user", cascade="all, delete-orphan")


# ── Sessions ────────────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    id:           Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uid)
    user_id:      Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash:   Mapped[str]      = mapped_column(String(128), unique=True, nullable=False)
    device_name:  Mapped[str | None] = mapped_column(String(128))
    created_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked:   Mapped[bool]     = mapped_column(Boolean, default=False)

    user: Mapped["User"] = relationship(back_populates="sessions")


# ── Conversations (DMs) ─────────────────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("uq_conversation_pair", "user_a_id", "user_b_id", unique=True),
    )

    id:        Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uid)
    user_a_id: Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False)
    user_b_id: Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.timestamp",
    )


# ── Messages ────────────────────────────────────────────────────────────────

class Message(Base):
    __tablename__ = "messages"

    id:              Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uid)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), index=True)
    group_id:        Mapped[str | None] = mapped_column(ForeignKey("groups.id"), index=True)
    sender_id:       Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False)

    # E2EE fields — server stores ciphertext only, never plaintext
    ciphertext:      Mapped[str]      = mapped_column(Text, nullable=False)
    nonce:           Mapped[str]      = mapped_column(String(32), nullable=False)
    ephemeral_pub:   Mapped[str]      = mapped_column(Text, nullable=False)
    signature:       Mapped[str]      = mapped_column(Text, nullable=False)

    timestamp:       Mapped[float]    = mapped_column(Float, nullable=False, index=True)
    delivered:       Mapped[bool]     = mapped_column(Boolean, default=False)
    read:            Mapped[bool]     = mapped_column(Boolean, default=False)
    read_at:         Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sender:       Mapped["User"]         = relationship(back_populates="sent_messages")
    conversation: Mapped["Conversation | None"] = relationship(back_populates="messages")
    group:        Mapped["Group | None"]        = relationship(back_populates="messages")


# ── Groups ──────────────────────────────────────────────────────────────────

class Group(Base):
    __tablename__ = "groups"

    id:         Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uid)
    name:       Mapped[str]      = mapped_column(String(128), nullable=False)
    creator_id: Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Encrypted group key bundle (JSON): {user_id: encrypted_group_key}
    key_bundle: Mapped[str | None] = mapped_column(Text)

    members:  Mapped[list["GroupMember"]] = relationship(back_populates="group", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]]     = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (
        Index("uq_group_member", "group_id", "user_id", unique=True),
    )

    id:        Mapped[str]      = mapped_column(String(36), primary_key=True, default=_uid)
    group_id:  Mapped[str]      = mapped_column(ForeignKey("groups.id"), nullable=False, index=True)
    user_id:   Mapped[str]      = mapped_column(ForeignKey("users.id"), nullable=False)
    role:      Mapped[str]      = mapped_column(Enum("admin", "member", name="member_role"), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    group: Mapped["Group"] = relationship(back_populates="members")
    user:  Mapped["User"]  = relationship(back_populates="group_memberships")

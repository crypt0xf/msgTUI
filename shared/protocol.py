"""
Wire-protocol definitions shared between server and client.
All WebSocket frames are JSON-encoded WsEnvelope objects.
"""
from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field
import time
import uuid


# ── Message types ──────────────────────────────────────────────────────────

class MsgType(str, Enum):
    # Client → Server
    AUTH          = "auth"
    SEND_DM       = "send_dm"
    SEND_GROUP    = "send_group"
    TYPING        = "typing"
    READ_ACK      = "read_ack"
    PING          = "ping"

    # Server → Client
    AUTHENTICATED = "authenticated"
    MESSAGE       = "message"
    GROUP_MESSAGE = "group_message"
    DELIVERY_ACK  = "delivery_ack"
    READ_RECEIPT  = "read_receipt"
    TYPING_IND    = "typing_indicator"
    USER_STATUS   = "user_status"
    PONG          = "pong"
    ERROR         = "error"
    KEY_BUNDLE    = "key_bundle"


# ── Ciphertext envelope (E2EE payload) ─────────────────────────────────────

class E2EEPayload(BaseModel):
    ciphertext:         str   # base64-encoded AES-256-GCM ciphertext
    nonce:              str   # base64-encoded 12-byte GCM nonce
    ephemeral_pub:      str   # base64-encoded sender ephemeral X25519 public key
    signature:          str   # base64-encoded Ed25519 signature
    sender_id:          str
    message_id:         str   = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:          float = Field(default_factory=time.time)


# ── WebSocket envelope ──────────────────────────────────────────────────────

class WsEnvelope(BaseModel):
    type:    MsgType
    payload: dict[str, Any] = Field(default_factory=dict)

    def json_bytes(self) -> bytes:
        return self.model_dump_json().encode()


# ── REST response helpers ───────────────────────────────────────────────────

class ApiError(BaseModel):
    code:    str
    message: str


class ApiOk(BaseModel):
    ok: bool = True

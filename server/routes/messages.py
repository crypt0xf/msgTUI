"""Message and conversation endpoints."""
from __future__ import annotations
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.crypto import validate_e2ee_fields
from server.database import get_db
from server.models import Conversation, Message, User
from server.routes.auth import get_current_user
from server.schemas import ConversationOut, MessageHistoryRequest, MessageOut
from server.websocket_manager import manager
from shared.protocol import E2EEPayload, MsgType, WsEnvelope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/messages", tags=["messages"])


# ── Conversations list ────────────────────────────────────────────────────────

@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.scalars(
        select(Conversation)
        .where(or_(Conversation.user_a_id == current_user.id, Conversation.user_b_id == current_user.id))
        .options(selectinload(Conversation.messages))
        .order_by(Conversation.created_at.desc())
    )
    convs = list(result)
    out = []
    for conv in convs:
        peer_id = conv.user_b_id if conv.user_a_id == current_user.id else conv.user_a_id
        peer = await db.get(User, peer_id)
        if peer:
            out.append(ConversationOut(id=conv.id, peer=peer, created_at=conv.created_at))
    return out


# ── Get or create a DM conversation ──────────────────────────────────────────

@router.post("/conversations/{peer_id}", response_model=ConversationOut, status_code=201)
async def get_or_create_conversation(
    peer_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if peer_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot message yourself")
    peer = await db.get(User, peer_id)
    if not peer:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")

    a, b = sorted([current_user.id, peer_id])
    conv = await db.scalar(
        select(Conversation).where(Conversation.user_a_id == a, Conversation.user_b_id == b)
    )
    if not conv:
        conv = Conversation(user_a_id=a, user_b_id=b)
        db.add(conv)
        await db.commit()
        await db.refresh(conv)
    return ConversationOut(id=conv.id, peer=peer, created_at=conv.created_at)


# ── Message history ───────────────────────────────────────────────────────────

@router.post("/conversations/{conv_id}/history", response_model=list[MessageOut])
async def get_history(
    conv_id: str,
    req: MessageHistoryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _get_conversation_or_403(conv_id, current_user.id, db)
    query = select(Message).where(Message.conversation_id == conv_id)
    if req.before_timestamp:
        query = query.where(Message.timestamp < req.before_timestamp)
    query = query.order_by(Message.timestamp.desc()).limit(req.limit)
    result = await db.scalars(query)
    return list(result)


# ── Send DM via REST (offline delivery buffer) ────────────────────────────────

@router.post("/conversations/{conv_id}/send", response_model=MessageOut, status_code=201)
async def send_message(
    conv_id: str,
    payload: E2EEPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await _get_conversation_or_403(conv_id, current_user.id, db)
    if not validate_e2ee_fields(payload.ciphertext, payload.nonce, payload.ephemeral_pub, payload.signature):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Malformed E2EE fields")

    msg = Message(
        id=payload.message_id,
        conversation_id=conv_id,
        sender_id=current_user.id,
        ciphertext=payload.ciphertext,
        nonce=payload.nonce,
        ephemeral_pub=payload.ephemeral_pub,
        signature=payload.signature,
        timestamp=payload.timestamp or time.time(),
    )
    db.add(msg)
    await db.flush()

    peer_id = conv.user_b_id if conv.user_a_id == current_user.id else conv.user_a_id
    envelope = WsEnvelope(type=MsgType.MESSAGE, payload={
        "conversation_id": conv_id,
        **payload.model_dump(),
    })
    delivered = await manager.send(peer_id, envelope.json_bytes())
    if delivered:
        msg.delivered = True

    await db.commit()
    await db.refresh(msg)
    return msg


# ── Mark as read ──────────────────────────────────────────────────────────────

@router.post("/conversations/{conv_id}/read/{message_id}", status_code=204)
async def mark_read(
    conv_id: str,
    message_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    msg = await db.get(Message, message_id)
    if not msg or msg.conversation_id != conv_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    msg.read = True
    msg.read_at = datetime.now(timezone.utc)
    await db.commit()

    # Notify sender
    envelope = WsEnvelope(type=MsgType.READ_RECEIPT, payload={
        "message_id": message_id,
        "reader_id": current_user.id,
    })
    await manager.send(msg.sender_id, envelope.json_bytes())


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_conversation_or_403(conv_id: str, user_id: str, db: AsyncSession) -> Conversation:
    conv = await db.get(Conversation, conv_id)
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    if user_id not in (conv.user_a_id, conv.user_b_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a participant")
    return conv

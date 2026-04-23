"""
WebSocket endpoint.
Clients must send {"type":"auth","payload":{"token":"<access_token>"}}
within 10 seconds of connecting, otherwise the connection is closed.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from server import auth as auth_utils
from server.database import AsyncSessionLocal
from server.models import Conversation, Message, User
from server.websocket_manager import manager
from shared.protocol import MsgType, WsEnvelope

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

_AUTH_TIMEOUT = 10.0


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    user_id: str | None = None

    try:
        # ── Authentication handshake ───────────────────────────────────────
        try:
            frame = await asyncio.wait_for(ws.receive(), timeout=_AUTH_TIMEOUT)
            raw = frame.get("text") or frame.get("bytes", b"").decode()
        except asyncio.TimeoutError:
            await ws.close(code=4001, reason="auth timeout")
            return

        try:
            msg = json.loads(raw)
            assert msg["type"] == MsgType.AUTH
            payload = auth_utils.decode_token(msg["payload"]["token"], expected_type="access")
            user_id = payload["sub"]
        except Exception:
            await ws.close(code=4001, reason="invalid auth")
            return

        # Register connection
        await manager.connect(user_id, ws)

        # Confirm authentication
        ok = WsEnvelope(type=MsgType.AUTHENTICATED, payload={"user_id": user_id})
        await ws.send_bytes(ok.json_bytes())

        # Broadcast online status to all connected users
        await _broadcast_status(user_id, "online")

        # Deliver any undelivered messages
        await _deliver_pending(user_id)

        # ── Message loop ───────────────────────────────────────────────────
        while True:
            frame = await ws.receive()
            if frame["type"] == "websocket.disconnect":
                break
            raw = frame.get("text") or frame.get("bytes", b"").decode()
            if not raw:
                continue
            await _handle_message(user_id, raw)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.exception("ws error user=%s: %s", user_id, exc)
    finally:
        if user_id:
            await manager.disconnect(user_id, ws)
            await _broadcast_status(user_id, "offline")
            # Update last_seen
            async with AsyncSessionLocal() as db:
                user = await db.get(User, user_id)
                if user:
                    user.last_seen = datetime.now(timezone.utc)
                    await db.commit()


async def _handle_message(user_id: str, raw: str) -> None:
    try:
        data = json.loads(raw)
        msg_type = data.get("type")
        payload  = data.get("payload", {})
    except json.JSONDecodeError:
        return

    if msg_type == MsgType.TYPING:
        await _handle_typing(user_id, payload)
    elif msg_type == MsgType.READ_ACK:
        await _handle_read_ack(user_id, payload)
    elif msg_type == MsgType.PING:
        pong = WsEnvelope(type=MsgType.PONG, payload={"ts": time.time()})
        await manager.send(user_id, pong.json_bytes())
    # DM / group sends are handled via REST endpoints for persistence guarantees


async def _handle_typing(user_id: str, payload: dict) -> None:
    peer_id = payload.get("peer_id")
    group_id = payload.get("group_id")
    if peer_id:
        envelope = WsEnvelope(type=MsgType.TYPING_IND, payload={
            "sender_id": user_id,
            "peer_id": peer_id,
        })
        await manager.send(peer_id, envelope.json_bytes())
    elif group_id:
        async with AsyncSessionLocal() as db:
            from server.models import GroupMember
            members = await db.scalars(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id != user_id,
                )
            )
            member_ids = [m.user_id for m in members]
        envelope = WsEnvelope(type=MsgType.TYPING_IND, payload={
            "sender_id": user_id,
            "group_id": group_id,
        })
        await manager.broadcast(member_ids, envelope.json_bytes())


async def _handle_read_ack(user_id: str, payload: dict) -> None:
    message_id = payload.get("message_id")
    if not message_id:
        return
    async with AsyncSessionLocal() as db:
        from datetime import datetime, timezone
        msg = await db.get(Message, message_id)
        if msg and not msg.read:
            msg.read = True
            msg.read_at = datetime.now(timezone.utc)
            await db.commit()
            receipt = WsEnvelope(type=MsgType.READ_RECEIPT, payload={
                "message_id": message_id,
                "reader_id": user_id,
            })
            await manager.send(msg.sender_id, receipt.json_bytes())


async def _broadcast_status(user_id: str, status: str) -> None:
    envelope = WsEnvelope(type=MsgType.USER_STATUS, payload={
        "user_id": user_id,
        "status": status,
    })
    for uid in manager.online_users():
        if uid != user_id:
            await manager.send(uid, envelope.json_bytes())


async def _deliver_pending(user_id: str) -> None:
    """Push undelivered messages to a user who just came online."""
    async with AsyncSessionLocal() as db:
        msgs = await db.scalars(
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id, isouter=True)
            .where(
                Message.delivered == False,  # noqa: E712
                (
                    (Conversation.user_a_id == user_id) |
                    (Conversation.user_b_id == user_id)
                ),
            )
            .order_by(Message.timestamp)
            .limit(200)
        )
        for msg in msgs:
            envelope = WsEnvelope(type=MsgType.MESSAGE, payload={
                "message_id":      msg.id,
                "conversation_id": msg.conversation_id,
                "sender_id":       msg.sender_id,
                "ciphertext":      msg.ciphertext,
                "nonce":           msg.nonce,
                "ephemeral_pub":   msg.ephemeral_pub,
                "signature":       msg.signature,
                "timestamp":       msg.timestamp,
            })
            await manager.send(user_id, envelope.json_bytes())
            msg.delivered = True
        await db.commit()

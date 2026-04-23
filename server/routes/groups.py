"""Group endpoints: create, manage members, send messages."""
from __future__ import annotations
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.crypto import validate_e2ee_fields
from server.database import get_db
from server.models import Group, GroupMember, Message, User
from server.routes.auth import get_current_user
from server.schemas import (
    GroupCreate, GroupKeyBundle, GroupMemberAdd, GroupOut, MessageHistoryRequest,
    MessageOut,
)
from server.websocket_manager import manager
from shared.protocol import E2EEPayload, MsgType, WsEnvelope

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["groups"])


# ── List my groups ────────────────────────────────────────────────────────────

@router.get("", response_model=list[GroupOut])
async def list_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    memberships = await db.scalars(
        select(GroupMember)
        .where(GroupMember.user_id == current_user.id)
        .options(selectinload(GroupMember.group).selectinload(Group.members).selectinload(GroupMember.user))
    )
    return [_group_out(m.group) for m in memberships]


# ── Create group ──────────────────────────────────────────────────────────────

@router.post("", response_model=GroupOut, status_code=201)
async def create_group(
    req: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = Group(name=req.name, creator_id=current_user.id, key_bundle=req.key_bundle)
    db.add(group)
    await db.flush()

    all_member_ids = list({current_user.id, *req.member_ids})
    for uid in all_member_ids:
        role = "admin" if uid == current_user.id else "member"
        db.add(GroupMember(group_id=group.id, user_id=uid, role=role))

    await db.commit()
    refreshed = await db.scalar(
        select(Group)
        .where(Group.id == group.id)
        .options(selectinload(Group.members).selectinload(GroupMember.user))
    )
    return _group_out(refreshed)


# ── Get group ─────────────────────────────────────────────────────────────────

@router.get("/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_group_or_403(group_id, current_user.id, db)
    refreshed = await db.scalar(
        select(Group)
        .where(Group.id == group_id)
        .options(selectinload(Group.members).selectinload(GroupMember.user))
    )
    return _group_out(refreshed)


# ── Add member ────────────────────────────────────────────────────────────────

@router.post("/{group_id}/members", status_code=204)
async def add_member(
    group_id: str,
    req: GroupMemberAdd,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_403(group_id, current_user.id, db, require_admin=True)
    existing = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == req.user_id)
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    db.add(GroupMember(group_id=group_id, user_id=req.user_id))

    # Update key bundle if provided
    if req.encrypted_key_for:
        bundle = json.loads(group.key_bundle or "{}")
        bundle[req.user_id] = req.encrypted_key_for
        group.key_bundle = json.dumps(bundle)

    await db.commit()


# ── Remove member ─────────────────────────────────────────────────────────────

@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: str,
    user_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_403(group_id, current_user.id, db)
    if user_id != current_user.id:
        # Only admins can remove others
        await _assert_admin(group_id, current_user.id, db)

    membership = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    if not membership:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found")
    await db.delete(membership)
    await db.commit()


# ── Update key bundle (re-key after member removal) ──────────────────────────

@router.put("/{group_id}/key-bundle", status_code=204)
async def update_key_bundle(
    group_id: str,
    req: GroupKeyBundle,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_403(group_id, current_user.id, db, require_admin=True)
    group.key_bundle = req.key_bundle
    await db.commit()


# ── Get key bundle ────────────────────────────────────────────────────────────

@router.get("/{group_id}/key-bundle")
async def get_key_bundle(
    group_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_403(group_id, current_user.id, db)
    if not group.key_bundle:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No key bundle")
    bundle = json.loads(group.key_bundle)
    slice_ = bundle.get(current_user.id)
    if not slice_:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No key slice for this user")
    return {"encrypted_key": slice_}


# ── Message history ───────────────────────────────────────────────────────────

@router.post("/{group_id}/history", response_model=list[MessageOut])
async def group_history(
    group_id: str,
    req: MessageHistoryRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_group_or_403(group_id, current_user.id, db)
    query = select(Message).where(Message.group_id == group_id)
    if req.before_timestamp:
        query = query.where(Message.timestamp < req.before_timestamp)
    query = query.order_by(Message.timestamp.desc()).limit(req.limit)
    result = await db.scalars(query)
    return list(result)


# ── Send group message ────────────────────────────────────────────────────────

@router.post("/{group_id}/send", response_model=MessageOut, status_code=201)
async def send_group_message(
    group_id: str,
    payload: E2EEPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group = await _get_group_or_403(group_id, current_user.id, db)
    if not validate_e2ee_fields(payload.ciphertext, payload.nonce, payload.ephemeral_pub, payload.signature):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Malformed E2EE fields")

    await db.refresh(group, attribute_names=["members"])

    msg = Message(
        id=payload.message_id,
        group_id=group_id,
        sender_id=current_user.id,
        ciphertext=payload.ciphertext,
        nonce=payload.nonce,
        ephemeral_pub=payload.ephemeral_pub,
        signature=payload.signature,
        timestamp=payload.timestamp or time.time(),
    )
    db.add(msg)
    await db.flush()

    member_ids = [m.user_id for m in group.members if m.user_id != current_user.id]
    envelope = WsEnvelope(type=MsgType.GROUP_MESSAGE, payload={
        "group_id": group_id,
        **payload.model_dump(),
    })
    await manager.broadcast(member_ids, envelope.json_bytes())

    await db.commit()
    await db.refresh(msg)
    return msg


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_group_or_403(
    group_id: str, user_id: str, db: AsyncSession, require_admin: bool = False
) -> Group:
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    membership = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    if not membership:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member")
    if require_admin and membership.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")
    return group


async def _assert_admin(group_id: str, user_id: str, db: AsyncSession) -> None:
    from sqlalchemy import select
    m = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    if not m or m.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required")


def _group_out(group: Group) -> GroupOut:
    from server.schemas import UserPublic
    members = []
    for m in group.members:
        if hasattr(m, "user") and m.user:
            members.append(UserPublic.model_validate(m.user))
    return GroupOut(
        id=group.id,
        name=group.name,
        creator_id=group.creator_id,
        created_at=group.created_at,
        members=members,
    )

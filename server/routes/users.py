"""User endpoints: profile, search, public key bundles."""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.database import get_db
from server.models import User
from server.routes.auth import get_current_user
from server.schemas import KeyBundle, UserMe, UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserMe)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/search", response_model=list[UserPublic])
async def search_users(
    q: str = Query(min_length=2, max_length=64),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q_safe = q.lower().replace("%", "").replace("_", "\\_")
    result = await db.scalars(
        select(User)
        .where(User.username.like(f"%{q_safe}%"))
        .limit(20)
    )
    return list(result)


@router.get("/{user_id}", response_model=UserPublic)
async def get_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("/{user_id}/key-bundle", response_model=KeyBundle)
async def get_key_bundle(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Returns the E2EE public key bundle for a user."""
    user = await db.get(User, user_id)
    if not user or not user.pub_key_exchange or not user.pub_key_sign:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Key bundle not found")
    return KeyBundle(
        user_id=user.id,
        pub_key_exchange=user.pub_key_exchange,
        pub_key_sign=user.pub_key_sign,
    )

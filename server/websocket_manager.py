"""
In-memory WebSocket connection manager.
Tracks authenticated connections and provides broadcast/unicast helpers.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        # user_id -> list of active WebSocket connections (multi-device)
        self._connections: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(user_id, []).append(ws)
        logger.info("ws_connect user=%s total_users=%d", user_id, len(self._connections))

    async def disconnect(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._connections.get(user_id, [])
            if ws in conns:
                conns.remove(ws)
            if not conns:
                self._connections.pop(user_id, None)
        logger.info("ws_disconnect user=%s", user_id)

    def is_online(self, user_id: str) -> bool:
        return bool(self._connections.get(user_id))

    def online_users(self) -> list[str]:
        return list(self._connections.keys())

    async def send(self, user_id: str, data: str | bytes) -> bool:
        """Deliver to all connections of a user. Returns True if delivered to ≥1."""
        conns = list(self._connections.get(user_id, []))
        if not conns:
            return False
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                if isinstance(data, bytes):
                    await ws.send_bytes(data)
                else:
                    await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(user_id, ws)
        return len(conns) > len(dead)

    async def broadcast(self, user_ids: list[str], data: str | bytes) -> None:
        await asyncio.gather(*(self.send(uid, data) for uid in user_ids))


manager = ConnectionManager()

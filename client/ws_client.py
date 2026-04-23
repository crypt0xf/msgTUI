"""
Async WebSocket client.
Runs in a background asyncio task and delivers messages via a callback.
Handles automatic reconnection with exponential back-off.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosed

from client.config import get_settings
from shared.protocol import MsgType, WsEnvelope

logger = logging.getLogger(__name__)

MessageCallback = Callable[[dict], Awaitable[None]]


class WsClient:
    def __init__(self) -> None:
        self._token:    str = ""
        self._callback: MessageCallback | None = None
        self._ws  = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._reconnect_delay = 1.0
        self._max_delay       = 60.0

    def set_token(self, token: str) -> None:
        self._token = token

    def on_message(self, callback: MessageCallback) -> None:
        self._callback = callback

    def start(self) -> None:
        if not self._token:
            return
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._run_loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def send_typing(self, peer_id: str | None = None, group_id: str | None = None) -> None:
        payload: dict = {}
        if peer_id:
            payload["peer_id"] = peer_id
        elif group_id:
            payload["group_id"] = group_id
        await self._send(WsEnvelope(type=MsgType.TYPING, payload=payload))

    async def send_read_ack(self, message_id: str) -> None:
        await self._send(WsEnvelope(type=MsgType.READ_ACK, payload={"message_id": message_id}))

    async def ping(self) -> None:
        await self._send(WsEnvelope(type=MsgType.PING, payload={}))

    async def _send(self, envelope: WsEnvelope) -> None:
        if self._ws:
            try:
                await self._ws.send(envelope.model_dump_json())
            except Exception as exc:
                logger.debug("ws send error: %s", exc)

    async def _run_loop(self) -> None:
        cfg = get_settings()
        while self._running:
            try:
                uri = f"{cfg.ws_url}/ws"
                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0  # reset on success

                    # Authenticate
                    auth_msg = WsEnvelope(type=MsgType.AUTH, payload={"token": self._token})
                    await ws.send(auth_msg.model_dump_json())

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            data = json.loads(raw)
                            if self._callback:
                                await self._callback(data)
                        except Exception as exc:
                            logger.debug("ws message parse error: %s", exc)

            except asyncio.CancelledError:
                break
            except (ConnectionClosed, OSError, Exception) as exc:
                if not self._running:
                    break
                logger.warning("ws disconnected (%s), reconnecting in %.1fs", exc, self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_delay)
            finally:
                self._ws = None


ws_client = WsClient()

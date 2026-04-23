"""Async HTTP client wrapper para a API REST do msgTUI."""
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, Optional
import httpx

from client.config import get_settings


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail      = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class ApiClient:
    def __init__(self) -> None:
        cfg = get_settings()
        self._base        = cfg.server_url
        self._client: Optional[httpx.AsyncClient] = None
        self.access_token:  str = ""
        self.refresh_token: str = ""

    # ── Context manager ───────────────────────────────────────────────────────
    # ATENÇÃO: o singleton `api` NÃO deve ser usado como context manager
    # diretamente quando houver chamadas concorrentes — use `api_call()` abaixo.

    async def __aenter__(self):
        self._client = httpx.AsyncClient(base_url=self._base, timeout=15.0)
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Helpers internos ──────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.access_token:
            h["Authorization"] = f"Bearer {self.access_token}"
        return h

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        assert self._client, "ApiClient não está aberto — use async with api: ou api_call()"
        r = await self._client.request(method, path, headers=self._headers(), **kwargs)
        if r.status_code >= 400:
            try:
                body   = r.json()
                detail = body.get("detail") or body.get("message") or r.text
            except Exception:
                detail = r.text
            raise ApiError(r.status_code, detail)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return None

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def register(self, username: str, email: str, password: str, pub_exc: str, pub_sig: str) -> dict:
        data = await self._request("POST", "/auth/register", json={
            "username": username, "email": email, "password": password,
            "pub_key_exchange": pub_exc, "pub_key_sign": pub_sig,
        })
        self._store_tokens(data)
        return data

    async def login(self, username: str, password: str, totp_code: str = "", device_name: str = "tui") -> dict:
        body: dict = {"username": username, "password": password, "device_name": device_name}
        if totp_code:
            body["totp_code"] = totp_code
        data = await self._request("POST", "/auth/login", json=body)
        self._store_tokens(data)
        return data

    async def refresh(self) -> dict:
        data = await self._request("POST", "/auth/refresh", json={"refresh_token": self.refresh_token})
        self._store_tokens(data)
        return data

    async def logout(self) -> None:
        try:
            await self._request("POST", "/auth/logout", json={"refresh_token": self.refresh_token})
        except ApiError:
            pass
        finally:
            self.access_token  = ""
            self.refresh_token = ""

    def _store_tokens(self, data: dict) -> None:
        self.access_token  = data.get("access_token", "")
        self.refresh_token = data.get("refresh_token", "")

    # ── Users ─────────────────────────────────────────────────────────────────

    async def get_me(self) -> dict:
        return await self._request("GET", "/users/me")

    async def search_users(self, query: str) -> list[dict]:
        return await self._request("GET", "/users/search", params={"q": query})

    async def get_key_bundle(self, user_id: str) -> dict:
        return await self._request("GET", f"/users/{user_id}/key-bundle")

    async def get_user(self, user_id: str) -> dict:
        return await self._request("GET", f"/users/{user_id}")

    # ── Conversas ─────────────────────────────────────────────────────────────

    async def list_conversations(self) -> list[dict]:
        return await self._request("GET", "/messages/conversations")

    async def get_or_create_conversation(self, peer_id: str) -> dict:
        return await self._request("POST", f"/messages/conversations/{peer_id}")

    async def get_history(self, conv_id: str, before: float | None = None, limit: int = 50) -> list[dict]:
        body: dict = {"limit": limit}
        if before:
            body["before_timestamp"] = before
        return await self._request("POST", f"/messages/conversations/{conv_id}/history", json=body)

    async def send_message(self, conv_id: str, e2ee_payload: dict) -> dict:
        return await self._request("POST", f"/messages/conversations/{conv_id}/send", json=e2ee_payload)

    async def mark_read(self, conv_id: str, message_id: str) -> None:
        await self._request("POST", f"/messages/conversations/{conv_id}/read/{message_id}")

    # ── Grupos ────────────────────────────────────────────────────────────────

    async def list_groups(self) -> list[dict]:
        return await self._request("GET", "/groups")

    async def create_group(self, name: str, member_ids: list[str], key_bundle: str) -> dict:
        return await self._request("POST", "/groups", json={
            "name": name, "member_ids": member_ids, "key_bundle": key_bundle,
        })

    async def get_group(self, group_id: str) -> dict:
        return await self._request("GET", f"/groups/{group_id}")

    async def get_group_key_bundle(self, group_id: str) -> dict:
        return await self._request("GET", f"/groups/{group_id}/key-bundle")

    async def send_group_message(self, group_id: str, e2ee_payload: dict) -> dict:
        return await self._request("POST", f"/groups/{group_id}/send", json=e2ee_payload)

    async def get_group_history(self, group_id: str, before: float | None = None, limit: int = 50) -> list[dict]:
        body: dict = {"limit": limit}
        if before:
            body["before_timestamp"] = before
        return await self._request("POST", f"/groups/{group_id}/history", json=body)

    async def add_group_member(self, group_id: str, user_id: str, encrypted_key: str) -> None:
        await self._request("POST", f"/groups/{group_id}/members", json={
            "user_id": user_id, "encrypted_key_for": encrypted_key,
        })

    async def remove_group_member(self, group_id: str, user_id: str) -> None:
        await self._request("DELETE", f"/groups/{group_id}/members/{user_id}")


# Singleton global — tokens são atualizados após login
api = ApiClient()


@asynccontextmanager
async def api_call():
    """
    Context manager seguro para uso concorrente.
    Cria uma sessão httpx isolada a cada chamada, sem compartilhar _client.
    Usa os tokens do singleton `api`.

    Uso:
        async with api_call() as c:
            result = await c.list_groups()
    """
    session = ApiClient()
    session._base        = api._base
    session.access_token  = api.access_token
    session.refresh_token = api.refresh_token
    async with session:
        yield session

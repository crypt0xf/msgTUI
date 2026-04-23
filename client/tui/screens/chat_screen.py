"""
Main chat screen.
Orchestrates sidebar, message list, and message input.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

from client.api_client import ApiError, api, api_call
from client import crypto as _crypto
from client.key_store import key_store
from client.tui.widgets.message_input import MessageInput
from client.tui.widgets.message_list import MessageList
from client.tui.widgets.sidebar import Sidebar
from client.tui.screens.group_screen import CreateGroupScreen
from client.ws_client import ws_client

logger = logging.getLogger(__name__)


class ChatScreen(Screen):

    CSS = """
    #chat-layout {
        height: 100%;
        width: 100%;
    }
    #right-panel {
        height: 100%;
        width: 1fr;
        layout: vertical;
    }
    #conv-header {
        height: 3;
        border-bottom: solid $primary-darken-2;
        padding: 0 2;
        background: $surface-darken-1;
        content-align: left middle;
        color: $accent;
        text-style: bold;
    }
    #status-bar {
        height: 1;
        background: $surface-darken-2;
        padding: 0 2;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit",          "Sair"),
        Binding("ctrl+n", "focus_search",  "Nova conversa"),
        Binding("ctrl+g", "new_group",     "Novo grupo"),
        Binding("ctrl+l", "focus_sidebar", "Sidebar"),
        Binding("escape", "focus_input",   "Focar input"),
    ]

    def __init__(self, access_token: str, refresh_token: str, user: dict) -> None:
        super().__init__()
        self._access_token  = access_token
        self._refresh_token = refresh_token
        self._user          = user
        self._current_conv_id:   Optional[str] = None
        self._current_conv_name: str = ""
        self._current_is_group:  bool = False
        self._key_cache: dict[str, dict] = {}
        self._typing_task: Optional[asyncio.Task] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="chat-layout"):
            yield Sidebar()
            with Vertical(id="right-panel"):
                yield Static("Selecione ou abra uma conversa  (Ctrl+N)", id="conv-header")
                yield Static("", id="status-bar")
                yield MessageList()
                yield MessageInput()
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one(MessageList).set_identity(self._user["id"], self._user["username"])
        api.access_token  = self._access_token
        api.refresh_token = self._refresh_token
        ws_client.set_token(self._access_token)
        ws_client.on_message(self._on_ws_message)
        ws_client.start()
        self.run_worker(self._load_sidebar(), exclusive=False)

    # ── Sidebar ───────────────────────────────────────────────────────────────

    async def _load_sidebar(self) -> None:
        try:
            async with api_call() as c:
                convs  = await c.list_conversations()
                groups = await c.list_groups()
            await self.query_one(Sidebar).load_conversations(convs, groups)
        except Exception as exc:
            self._set_status(f"Erro ao carregar conversas: {exc}")

    def on_sidebar_conversation_selected(self, event: Sidebar.ConversationSelected) -> None:
        self._open_conversation(event.conv_id, event.name, event.is_group)

    def on_sidebar_open_dm(self, event: Sidebar.OpenDM) -> None:
        """Usuário digitou um nome e pressionou Enter na busca."""
        self.run_worker(self._start_dm_by_username(event.username), exclusive=False)

    async def _start_dm_by_username(self, username: str) -> None:
        self._set_status(f"Procurando '{username}'...")
        try:
            async with api_call() as c:
                results = await c.search_users(username)
        except Exception as exc:
            self._set_status(f"Erro na busca: {exc}")
            return

        # Procura match exato primeiro, depois parcial
        match = next((u for u in results if u["username"] == username.lower()), None)
        if match is None and results:
            match = results[0]
        if match is None:
            self._set_status(f"Usuario '{username}' nao encontrado")
            return
        if match["id"] == self._user["id"]:
            self._set_status("Voce nao pode conversar consigo mesmo")
            return

        self._set_status(f"Abrindo conversa com {match['username']}...")
        try:
            async with api_call() as c:
                conv = await c.get_or_create_conversation(match["id"])
        except Exception as exc:
            self._set_status(f"Erro ao abrir conversa: {exc}")
            return

        await self._load_sidebar()
        self._open_conversation(conv["id"], match["username"], is_group=False)

    def _open_conversation(self, conv_id: str, name: str, is_group: bool) -> None:
        self._current_conv_id   = conv_id
        self._current_conv_name = name
        self._current_is_group  = is_group
        prefix = "[GRUPO] " if is_group else ""
        self.query_one("#conv-header", Static).update(f"{prefix}{name}")
        self.query_one(MessageList).clear_messages()
        self.query_one(MessageInput).enable()
        self._set_status("")
        self.run_worker(self._load_history(), exclusive=True)

    # ── Histórico ─────────────────────────────────────────────────────────────

    async def _load_history(self) -> None:
        if not self._current_conv_id:
            return
        self._set_status("Carregando mensagens...")
        try:
            async with api_call() as c:
                if self._current_is_group:
                    messages = await c.get_group_history(self._current_conv_id)
                else:
                    messages = await c.get_history(self._current_conv_id)
            for msg in reversed(messages):
                await self._display_message(msg, is_group=self._current_is_group)
            self._set_status(f"{len(messages)} mensagem(ns) carregada(s)")
        except Exception as exc:
            self._set_status(f"Erro ao carregar historico: {exc}")

    async def _display_message(self, msg: dict, is_group: bool) -> None:
        sender_id = msg["sender_id"]
        try:
            bundle = await self._get_key_bundle(sender_id)
            if is_group:
                group_key = key_store.get_group_key(self._current_conv_id)
                if not group_key:
                    async with api_call() as c:
                        kb = await c.get_group_key_bundle(self._current_conv_id)
                    group_key = _crypto.decrypt_group_key(
                        key_slice_json=json.dumps(json.loads(kb["encrypted_key"])),
                        my_priv_exchange=key_store.get_exc_priv(),
                        my_pub_exchange_b64=key_store.get_exc_pub_b64(),
                    )
                    key_store.save_group_key(self._current_conv_id, group_key)
                text = _crypto.decrypt_group_message(
                    ciphertext_b64=msg["ciphertext"],
                    nonce_b64=msg["nonce"],
                    signature_b64=msg["signature"],
                    sender_pub_sign_b64=bundle["pub_key_sign"],
                    group_key=group_key,
                    group_id=self._current_conv_id,
                )
            else:
                text = _crypto.decrypt_dm(
                    ciphertext_b64=msg["ciphertext"],
                    nonce_b64=msg["nonce"],
                    ephemeral_pub_b64=msg["ephemeral_pub"],
                    signature_b64=msg["signature"],
                    my_priv_exchange=key_store.get_exc_priv(),
                    my_pub_exchange_b64=key_store.get_exc_pub_b64(),
                    sender_pub_sign_b64=bundle["pub_key_sign"],
                )
            error = False
        except Exception as exc:
            text  = "[erro de descriptografia]"
            error = True
            logger.warning("decrypt history msg %s: %s", msg.get("id"), exc)

        sender_name = "Eu" if sender_id == self._user["id"] else bundle.get("username", sender_id[:8])
        self.query_one(MessageList).add_message(
            message_id=msg["id"],
            sender_id=sender_id,
            sender_name=sender_name,
            text=text,
            timestamp=msg["timestamp"],
            error=error,
        )

    # ── Envio ─────────────────────────────────────────────────────────────────

    def on_message_input_send_message(self, event: MessageInput.SendMessage) -> None:
        text = event.text.strip()
        if text.startswith("/"):
            self.run_worker(self._handle_command(text), exclusive=False)
        else:
            self.run_worker(self._do_send(text), exclusive=False)

    async def _handle_command(self, text: str) -> None:
        """Interpreta comandos /cmd dentro do chat."""
        parts = text.split(maxsplit=1)
        cmd   = parts[0].lower()
        arg   = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/ajuda":
            self._set_status(
                "/membros  /adicionar <user>  /remover <user>  /novo-grupo  /ajuda"
            )

        elif cmd == "/membros":
            await self._cmd_list_members()

        elif cmd == "/adicionar":
            if not arg:
                self._set_status("Uso: /adicionar <username>")
                return
            await self._cmd_add_member(arg)

        elif cmd == "/remover":
            if not arg:
                self._set_status("Uso: /remover <username>")
                return
            await self._cmd_remove_member(arg)

        elif cmd == "/novo-grupo":
            self.action_new_group()

        else:
            self._set_status(f"Comando desconhecido: {cmd}  (use /ajuda)")

    async def _cmd_list_members(self) -> None:
        if not self._current_is_group:
            self._set_status("Este comando so funciona em grupos")
            return
        try:
            async with api_call() as c:
                group = await c.get_group(self._current_conv_id)
            members = group.get("members", [])
            names   = [f"{m['username']}{'*' if m['id'] == group['creator_id'] else ''}"
                       for m in members]
            self._set_status("Membros: " + ", ".join(names) + "  (*=admin)")
        except Exception as exc:
            self._set_status(f"Erro: {exc}")

    async def _cmd_add_member(self, username: str) -> None:
        if not self._current_is_group:
            self._set_status("Este comando so funciona em grupos")
            return
        self._set_status(f"Adicionando '{username}'...")
        try:
            async with api_call() as c:
                results = await c.search_users(username)
            match = next((u for u in results if u["username"] == username.lower()), None)
            if not match:
                self._set_status(f"Usuario '{username}' nao encontrado")
                return

            async with api_call() as c:
                bundle = await c.get_key_bundle(match["id"])

            # Cifrar a chave do grupo para o novo membro
            group_key = key_store.get_group_key(self._current_conv_id)
            if not group_key:
                async with api_call() as c:
                    kb = await c.get_group_key_bundle(self._current_conv_id)
                group_key = _crypto.decrypt_group_key(
                    key_slice_json=json.dumps(json.loads(kb["encrypted_key"])),
                    my_priv_exchange=key_store.get_exc_priv(),
                    my_pub_exchange_b64=key_store.get_exc_pub_b64(),
                )
                key_store.save_group_key(self._current_conv_id, group_key)

            enc_slice = _crypto.encrypt_group_key_for_member(
                group_key, bundle["pub_key_exchange"]
            )
            async with api_call() as c:
                await c.add_group_member(
                    self._current_conv_id,
                    match["id"],
                    json.dumps(enc_slice),
                )
            self._set_status(f"'{match['username']}' adicionado ao grupo")
            await self._load_sidebar()
        except Exception as exc:
            self._set_status(f"Erro ao adicionar: {exc}")

    async def _cmd_remove_member(self, username: str) -> None:
        if not self._current_is_group:
            self._set_status("Este comando so funciona em grupos")
            return
        self._set_status(f"Removendo '{username}'...")
        try:
            async with api_call() as c:
                group = await c.get_group(self._current_conv_id)
            members = group.get("members", [])
            match = next((m for m in members if m["username"] == username.lower()), None)
            if not match:
                self._set_status(f"'{username}' nao e membro deste grupo")
                return
            async with api_call() as c:
                await c.remove_group_member(self._current_conv_id, match["id"])
            self._set_status(f"'{username}' removido do grupo")
            await self._load_sidebar()
        except Exception as exc:
            self._set_status(f"Erro ao remover: {exc}")

    async def _do_send(self, text: str) -> None:
        if not self._current_conv_id or not text:
            return
        try:
            if self._current_is_group:
                await self._send_group_message(text)
            else:
                await self._send_dm(text)
        except Exception as exc:
            self._set_status(f"Erro ao enviar: {exc}")
            logger.exception("Send failed")

    async def _send_dm(self, text: str) -> None:
        bundle = await self._resolve_peer_bundle_for_conv(self._current_conv_id)
        if not bundle:
            self._set_status("Nao foi possivel obter chaves do destinatario")
            return
        e2ee = _crypto.encrypt_dm(
            plaintext=text,
            recipient_pub_exchange_b64=bundle["pub_key_exchange"],
            sender_priv_sign=key_store.get_sig_priv(),
            sender_pub_exchange_b64=key_store.get_exc_pub_b64(),
        )
        payload = {**e2ee, "sender_id": self._user["id"],
                   "message_id": str(uuid.uuid4()), "timestamp": time.time()}
        async with api_call() as c:
            msg = await c.send_message(self._current_conv_id, payload)
        self.query_one(MessageList).add_message(
            message_id=msg["id"], sender_id=self._user["id"],
            sender_name="Eu", text=text, timestamp=msg["timestamp"],
        )

    async def _send_group_message(self, text: str) -> None:
        group_key = key_store.get_group_key(self._current_conv_id)
        if not group_key:
            async with api_call() as c:
                kb = await c.get_group_key_bundle(self._current_conv_id)
            group_key = _crypto.decrypt_group_key(
                key_slice_json=json.dumps(json.loads(kb["encrypted_key"])),
                my_priv_exchange=key_store.get_exc_priv(),
                my_pub_exchange_b64=key_store.get_exc_pub_b64(),
            )
            key_store.save_group_key(self._current_conv_id, group_key)
        e2ee = _crypto.encrypt_group_message(
            plaintext=text, group_key=group_key,
            sender_priv_sign=key_store.get_sig_priv(),
            group_id=self._current_conv_id,
        )
        payload = {**e2ee, "sender_id": self._user["id"],
                   "message_id": str(uuid.uuid4()), "timestamp": time.time()}
        async with api_call() as c:
            msg = await c.send_group_message(self._current_conv_id, payload)
        self.query_one(MessageList).add_message(
            message_id=msg["id"], sender_id=self._user["id"],
            sender_name="Eu", text=text, timestamp=msg["timestamp"],
        )

    # ── WebSocket ─────────────────────────────────────────────────────────────

    async def _on_ws_message(self, data: dict) -> None:
        t = data.get("type")
        p = data.get("payload", {})
        if t == "message":
            await self._handle_incoming_dm(p)
        elif t == "group_message":
            await self._handle_incoming_group(p)
        elif t == "typing_indicator":
            self._handle_typing_indicator(p)
        elif t == "read_receipt":
            self._set_status("Mensagem lida pelo destinatario")
        elif t == "user_status":
            uid, st = p.get("user_id", ""), p.get("status", "")
            if uid and st:
                self._set_status(f"{uid[:10]}... esta {st}")

    async def _handle_incoming_dm(self, payload: dict) -> None:
        sender_id = payload.get("sender_id")
        conv_id   = payload.get("conversation_id")
        if sender_id == self._user["id"]:
            return
        try:
            bundle    = await self._get_key_bundle(sender_id)
            plaintext = _crypto.decrypt_dm(
                ciphertext_b64=payload["ciphertext"],
                nonce_b64=payload["nonce"],
                ephemeral_pub_b64=payload["ephemeral_pub"],
                signature_b64=payload["signature"],
                my_priv_exchange=key_store.get_exc_priv(),
                my_pub_exchange_b64=key_store.get_exc_pub_b64(),
                sender_pub_sign_b64=bundle["pub_key_sign"],
            )
            error = False
        except Exception as exc:
            bundle    = {"username": sender_id[:8]}
            plaintext = "[erro de descriptografia]"
            error     = True
            logger.warning("DM decrypt: %s", exc)

        sender_name = bundle.get("username", sender_id[:8])
        if conv_id == self._current_conv_id:
            ml = self.query_one(MessageList)
            ml.add_message(
                message_id=payload.get("message_id", ""),
                sender_id=sender_id, sender_name=sender_name,
                text=plaintext, timestamp=payload.get("timestamp", time.time()),
                error=error,
            )
            if payload.get("message_id"):
                asyncio.create_task(ws_client.send_read_ack(payload["message_id"]))
        else:
            # Notificação de mensagem em outra conversa
            self._set_status(f"Nova mensagem de {sender_name}  (Ctrl+N para abrir)")
            self.query_one(Sidebar).set_unread(conv_id, 1)
            await self._load_sidebar()

    async def _handle_incoming_group(self, payload: dict) -> None:
        sender_id = payload.get("sender_id")
        group_id  = payload.get("group_id")
        if sender_id == self._user["id"]:
            return
        try:
            group_key = key_store.get_group_key(group_id)
            if not group_key:
                async with api_call() as c:
                    kb = await c.get_group_key_bundle(group_id)
                group_key = _crypto.decrypt_group_key(
                    key_slice_json=json.dumps(json.loads(kb["encrypted_key"])),
                    my_priv_exchange=key_store.get_exc_priv(),
                    my_pub_exchange_b64=key_store.get_exc_pub_b64(),
                )
                key_store.save_group_key(group_id, group_key)
            bundle    = await self._get_key_bundle(sender_id)
            plaintext = _crypto.decrypt_group_message(
                ciphertext_b64=payload["ciphertext"],
                nonce_b64=payload["nonce"],
                signature_b64=payload["signature"],
                sender_pub_sign_b64=bundle["pub_key_sign"],
                group_key=group_key, group_id=group_id,
            )
            error = False
        except Exception as exc:
            bundle    = {"username": sender_id[:8]}
            plaintext = "[erro de descriptografia]"
            error     = True
            logger.warning("Group decrypt: %s", exc)

        sender_name = bundle.get("username", sender_id[:8])
        if group_id == self._current_conv_id:
            self.query_one(MessageList).add_message(
                message_id=payload.get("message_id", ""),
                sender_id=sender_id, sender_name=sender_name,
                text=plaintext, timestamp=payload.get("timestamp", time.time()),
                error=error,
            )

    def _handle_typing_indicator(self, payload: dict) -> None:
        sender_id = payload.get("sender_id", "")
        group_id  = payload.get("group_id")
        if group_id == self._current_conv_id or payload.get("peer_id"):
            ml = self.query_one(MessageList)
            ml.show_typing(sender_id[:10])
            self.set_timer(3.0, ml.hide_typing)

    # ── Typing debounce ───────────────────────────────────────────────────────

    def on_message_input_typing_started(self, _: MessageInput.TypingStarted) -> None:
        if self._typing_task:
            self._typing_task.cancel()
        self._typing_task = asyncio.create_task(self._send_typing_debounced())

    async def _send_typing_debounced(self) -> None:
        await asyncio.sleep(0.5)
        if not self._current_conv_id:
            return
        if self._current_is_group:
            await ws_client.send_typing(group_id=self._current_conv_id)
        else:
            await ws_client.send_typing(peer_id=self._current_conv_id)

    # ── Cache de chaves ───────────────────────────────────────────────────────

    async def _get_key_bundle(self, user_id: str) -> dict:
        if user_id not in self._key_cache:
            try:
                async with api_call() as c:
                    bundle = await c.get_key_bundle(user_id)
                    user   = await c.get_user(user_id)
                self._key_cache[user_id] = {**bundle, "username": user.get("username", user_id[:8])}
            except Exception:
                self._key_cache[user_id] = {"pub_key_sign": "", "pub_key_exchange": "", "username": user_id[:8]}
        return self._key_cache[user_id]

    async def _resolve_peer_bundle_for_conv(self, conv_id: str) -> Optional[dict]:
        try:
            async with api_call() as sess:
                convs = await sess.list_conversations()
            for c in convs:
                if c["id"] == conv_id:
                    return await self._get_key_bundle(c["peer"]["id"])
        except Exception as exc:
            logger.error("resolve peer: %s", exc)
        return None

    # ── Ações de teclado ──────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        self._set_status("Digite o username e pressione Enter para abrir uma conversa")
        self.query_one("#search-input", Input).focus()

    def action_new_group(self) -> None:
        def _on_created(group: dict | None) -> None:
            if group:
                self.run_worker(self._after_group_created(group), exclusive=False)
        self.app.push_screen(CreateGroupScreen(), _on_created)

    async def _after_group_created(self, group: dict) -> None:
        await self._load_sidebar()
        self._open_conversation(group["id"], group["name"], is_group=True)

    def action_focus_sidebar(self) -> None:
        self.query_one(Sidebar).focus()

    def action_focus_input(self) -> None:
        self.query_one(MessageInput).query_one("#msg-input").focus()

    def action_quit(self) -> None:
        ws_client.stop()
        self.app.exit()

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-bar", Static).update(msg)

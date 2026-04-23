"""Tela de criação de grupos com distribuição E2EE de chave."""
from __future__ import annotations
import json
import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from client.api_client import api_call
from client import crypto as _crypto
from client.key_store import key_store

logger = logging.getLogger(__name__)


class MemberItem(ListItem):
    def __init__(self, user_id: str, username: str) -> None:
        super().__init__()
        self.user_id  = user_id
        self.username = username

    def compose(self) -> ComposeResult:
        yield Static(f"  {self.username}  [X para remover]")


class CreateGroupScreen(Screen):
    """Modal para criar um novo grupo."""

    CSS = """
    CreateGroupScreen {
        align: center middle;
    }
    #box {
        width: 60;
        height: auto;
        max-height: 36;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    .label {
        color: $text-muted;
        margin-top: 1;
    }
    Input { margin-top: 0; }
    #member-search-row {
        height: auto;
    }
    #btn-add-member {
        width: 12;
        margin-left: 1;
    }
    #members-title {
        color: $text-muted;
        margin-top: 1;
    }
    #members-list {
        height: auto;
        max-height: 8;
        border: solid $primary-darken-2;
        margin-top: 0;
    }
    #status {
        color: $warning;
        margin-top: 1;
        height: auto;
    }
    #btn-row {
        height: auto;
        margin-top: 1;
    }
    #btn-create { width: 1fr; }
    #btn-cancel { width: 1fr; margin-left: 1; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancelar")]

    def __init__(self) -> None:
        super().__init__()
        self._members: list[dict] = []   # [{id, username, pub_key_exchange, pub_key_sign}]

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static("Criar Novo Grupo", id="title")

            yield Label("Nome do grupo", classes="label")
            yield Input(placeholder="Ex: Familia, Trabalho, Amigos...", id="group-name")

            yield Label("Adicionar membros (username)", classes="label")
            with Horizontal(id="member-search-row"):
                yield Input(placeholder="username do membro", id="member-search")
                yield Button("Adicionar", id="btn-add-member", variant="primary")

            yield Static("Membros adicionados:", id="members-title")
            yield ListView(id="members-list")

            yield Static("", id="status")

            with Horizontal(id="btn-row"):
                yield Button("Criar Grupo", id="btn-create", variant="success")
                yield Button("Cancelar",    id="btn-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-add-member":
            self.run_worker(self._add_member(), exclusive=False)
        elif event.button.id == "btn-create":
            self.run_worker(self._create_group(), exclusive=False)
        elif event.button.id == "btn-cancel":
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "member-search":
            self.run_worker(self._add_member(), exclusive=False)
        elif event.input.id == "group-name":
            self.run_worker(self._create_group(), exclusive=False)

    async def _add_member(self) -> None:
        username = self.query_one("#member-search", Input).value.strip().lower()
        if not username:
            return

        # Não duplicar
        if any(m["username"] == username for m in self._members):
            self._status(f"'{username}' ja foi adicionado")
            return

        self._status(f"Buscando '{username}'...")
        try:
            async with api_call() as c:
                results = await c.search_users(username)
                match = next((u for u in results if u["username"] == username), None)
                if not match:
                    self._status(f"Usuario '{username}' nao encontrado")
                    return
                if key_store.is_loaded and match["id"] == key_store.user_id:
                    self._status("Voce e adicionado automaticamente como admin")
                    return
                bundle = await c.get_key_bundle(match["id"])
        except Exception as exc:
            self._status(f"Erro: {exc}")
            return

        self._members.append({
            "id":               match["id"],
            "username":         match["username"],
            "pub_key_exchange": bundle["pub_key_exchange"],
            "pub_key_sign":     bundle["pub_key_sign"],
        })
        lv = self.query_one("#members-list", ListView)
        lv.append(MemberItem(match["id"], match["username"]))
        self.query_one("#member-search", Input).value = ""
        self._status(f"'{match['username']}' adicionado")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Clique em membro na lista = remover."""
        item = event.item
        if isinstance(item, MemberItem):
            self._members = [m for m in self._members if m["id"] != item.user_id]
            item.remove()
            self._status(f"'{item.username}' removido da lista")

    async def _create_group(self) -> None:
        name = self.query_one("#group-name", Input).value.strip()
        if not name:
            self._status("Digite um nome para o grupo")
            return

        if not key_store.is_loaded:
            self._status("Erro: sessao nao iniciada. Faca login novamente.")
            logger.error("create_group: key_store not loaded")
            return

        try:
            my_uid = key_store.user_id
        except KeyError:
            self._status("Erro: user_id ausente no key store. Faca login novamente.")
            logger.error("create_group: user_id missing from key_store")
            return

        self._status("Gerando chaves E2EE e criando grupo...")
        logger.debug("create_group: name=%r uid=%s members=%d", name, my_uid, len(self._members))
        try:
            # 1. Buscar chave publica do proprio usuario + criar grupo num unico contexto
            async with api_call() as c:
                my_bundle = await c.get_key_bundle(my_uid)

                # 2. Gerar chave de grupo (AES-256) e cifrar para cada membro
                group_key   = _crypto.generate_group_key()
                all_members = [
                    {"id": my_uid, "pub_key_exchange": my_bundle["pub_key_exchange"]},
                    *self._members,
                ]
                key_bundle: dict[str, str] = {}
                for m in all_members:
                    enc_slice = _crypto.encrypt_group_key_for_member(
                        group_key, m["pub_key_exchange"]
                    )
                    key_bundle[m["id"]] = json.dumps(enc_slice)

                # 3. Criar grupo no servidor
                member_ids = [m["id"] for m in self._members]
                group = await c.create_group(
                    name=name,
                    member_ids=member_ids,
                    key_bundle=json.dumps(key_bundle),
                )

            # 4. Salvar chave de grupo localmente
            key_store.save_group_key(group["id"], group_key)
            logger.debug("create_group: done, group_id=%s", group["id"])

            self._status(f"Grupo '{name}' criado!")
            self.dismiss(group)

        except Exception as exc:
            logger.exception("create_group failed")
            self._status(f"Erro: {exc}")

    def _status(self, msg: str) -> None:
        self.query_one("#status", Static).update(msg)

    def action_cancel(self) -> None:
        self.dismiss(None)

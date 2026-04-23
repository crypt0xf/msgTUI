"""Sidebar: contact/group list + search."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, ListItem, ListView, Static


class ConversationItem(ListItem):
    def __init__(self, conv_id: str, label: str, is_group: bool = False, unread: int = 0) -> None:
        super().__init__()
        self.conv_id  = conv_id
        self.label    = label
        self.is_group = is_group
        self.unread   = unread

    def compose(self) -> ComposeResult:
        icon  = "G" if self.is_group else "D"
        badge = f" ({self.unread})" if self.unread else ""
        yield Static(f"[{icon}] {self.label}{badge}", markup=False)


class Sidebar(Widget):
    """Left sidebar showing DMs and groups."""

    CSS = """
    Sidebar {
        width: 26;
        height: 100%;
        border-right: solid $primary-darken-2;
        padding: 0;
        layout: vertical;
    }
    #search-input {
        margin: 0;
        border: none;
        border-bottom: solid $primary-darken-2;
    }
    #search-hint {
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        height: 1;
    }
    #sidebar-scroll {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    #section-label-dms, #section-label-groups {
        color: $text-muted;
        padding: 0 1;
        background: $surface-darken-1;
    }
    ListView {
        border: none;
        background: transparent;
        height: auto;
    }
    ListItem {
        padding: 0 1;
    }
    ListItem:hover { background: $primary-darken-2; }
    ListItem.--highlight { background: $primary; }
    """

    class ConversationSelected(Message):
        def __init__(self, conv_id: str, conv_name: str, is_group: bool) -> None:
            super().__init__()
            self.conv_id   = conv_id
            self.conv_name = conv_name
            self.is_group  = is_group

    class OpenDM(Message):
        """Emitida quando o usuário pressiona Enter na busca."""
        def __init__(self, username: str) -> None:
            super().__init__()
            self.username = username

    def __init__(self) -> None:
        super().__init__()
        self._conversations: list[dict] = []
        self._groups: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Buscar / abrir conversa…", id="search-input")
        yield Static("Enter = abrir DM com usuario", id="search-hint")
        with VerticalScroll(id="sidebar-scroll"):
            yield Label("MENSAGENS DIRETAS", id="section-label-dms")
            yield ListView(id="dm-list")
            yield Label("GRUPOS", id="section-label-groups")
            yield ListView(id="group-list")

    async def load_conversations(self, conversations: list[dict], groups: list[dict]) -> None:
        self._conversations = conversations
        self._groups = groups
        await self._render_lists()

    async def _render_lists(self) -> None:
        dm_list    = self.query_one("#dm-list", ListView)
        group_list = self.query_one("#group-list", ListView)

        # remove_children() is properly awaitable — avoids the race where
        # clear() defers individual remove() calls but append() mounts immediately.
        await dm_list.remove_children()
        await group_list.remove_children()

        for conv in self._conversations:
            peer = conv.get("peer", {})
            name = peer.get("username", "?")
            await dm_list.mount(ConversationItem(conv["id"], name, is_group=False))

        for grp in self._groups:
            await group_list.mount(ConversationItem(grp["id"], grp["name"], is_group=True))

    def set_unread(self, conv_id: str, count: int) -> None:
        for widget in self.query(ConversationItem):
            if widget.conv_id == conv_id:
                widget.unread = count
                widget.refresh()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ConversationItem):
            self.post_message(Sidebar.ConversationSelected(
                conv_id=item.conv_id,
                conv_name=item.label,
                is_group=item.is_group,
            ))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter na caixa de busca: abre DM com o usuário digitado."""
        username = event.value.strip()
        if username:
            self.post_message(Sidebar.OpenDM(username=username))
            event.input.value = ""

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filtra a lista enquanto digita."""
        self.run_worker(self._filter_lists(event.value.lower()), exclusive=True)

    async def _filter_lists(self, query: str) -> None:
        if not query:
            await self._render_lists()
            return

        filtered_convs  = [c for c in self._conversations
                           if query in c.get("peer", {}).get("username", "").lower()]
        filtered_groups = [g for g in self._groups
                           if query in g.get("name", "").lower()]

        dm_list    = self.query_one("#dm-list", ListView)
        group_list = self.query_one("#group-list", ListView)
        await dm_list.remove_children()
        await group_list.remove_children()
        for conv in filtered_convs:
            peer = conv.get("peer", {})
            await dm_list.mount(ConversationItem(conv["id"], peer.get("username", "?"), is_group=False))
        for grp in filtered_groups:
            await group_list.mount(ConversationItem(grp["id"], grp["name"], is_group=True))

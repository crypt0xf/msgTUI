"""Scrollable message view widget."""
from __future__ import annotations
from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.scroll_view import ScrollView
from textual.widget import Widget
from textual.widgets import Label, Static


def _fmt_time(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
    return dt.strftime("%H:%M")


class MessageBubble(Widget):
    CSS = """
    MessageBubble {
        height: auto;
        padding: 0 1;
        margin-bottom: 0;
    }
    .bubble-mine {
        align: right middle;
    }
    .bubble-theirs {
        align: left middle;
    }
    .sender-name {
        color: $accent;
        text-style: bold;
        margin-bottom: 0;
    }
    .msg-text-mine {
        background: $primary-darken-1;
        color: $text;
        padding: 0 1;
        border: round $primary;
    }
    .msg-text-theirs {
        background: $surface-darken-1;
        color: $text;
        padding: 0 1;
        border: round $primary-darken-3;
    }
    .msg-meta {
        color: $text-muted;
        text-style: italic;
    }
    .msg-error {
        color: $error;
        text-style: italic;
    }
    """

    def __init__(self, message_id: str, sender: str, text: str, timestamp: float, is_mine: bool, error: bool = False) -> None:
        super().__init__()
        self.message_id = message_id
        self.sender     = sender
        self.text       = text
        self.timestamp  = timestamp
        self.is_mine    = is_mine
        self.error      = error

    def compose(self) -> ComposeResult:
        time_str  = _fmt_time(self.timestamp)
        css_class = "bubble-mine" if self.is_mine else "bubble-theirs"
        txt_class = "msg-text-mine" if self.is_mine else "msg-text-theirs"

        if not self.is_mine:
            yield Label(self.sender, classes="sender-name")
        if self.error:
            yield Static(f"[🔐 Decryption error]", classes="msg-error")
        else:
            yield Static(self.text, classes=txt_class)
        meta = "✓" if self.is_mine else ""
        yield Label(f"{time_str} {meta}", classes="msg-meta")


class MessageList(Widget):
    """Scrollable list of decrypted message bubbles."""

    CSS = """
    MessageList {
        height: 1fr;
        overflow-y: auto;
        padding: 0 1;
    }
    #empty-state {
        text-align: center;
        color: $text-muted;
        margin-top: 4;
    }
    #typing-indicator {
        color: $text-muted;
        text-style: italic;
        padding: 0 1;
        height: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._my_user_id: str = ""
        self._my_username: str = ""

    def compose(self) -> ComposeResult:
        yield Static("Select a conversation", id="empty-state")
        yield Static("", id="typing-indicator")

    def set_identity(self, user_id: str, username: str) -> None:
        self._my_user_id = user_id
        self._my_username = username

    def clear_messages(self) -> None:
        for bubble in self.query(MessageBubble):
            bubble.remove()
        self.query_one("#empty-state", Static).update("")

    def add_message(
        self,
        message_id: str,
        sender_id: str,
        sender_name: str,
        text: str,
        timestamp: float,
        error: bool = False,
    ) -> None:
        is_mine = sender_id == self._my_user_id
        bubble  = MessageBubble(
            message_id=message_id,
            sender=sender_name,
            text=text,
            timestamp=timestamp,
            is_mine=is_mine,
            error=error,
        )
        # Insert before typing indicator
        typing = self.query_one("#typing-indicator", Static)
        self.mount(bubble, before=typing)
        self.scroll_end(animate=False)

    def show_typing(self, username: str) -> None:
        self.query_one("#typing-indicator", Static).update(f"{username} is typing…")

    def hide_typing(self) -> None:
        self.query_one("#typing-indicator", Static).update("")

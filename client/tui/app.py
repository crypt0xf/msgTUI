"""
Root Textual application.
Manages screen transitions and global message routing.
"""
from __future__ import annotations
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import Header, Footer

from client.tui.screens.auth_screen import AuthScreen
from client.tui.screens.chat_screen import ChatScreen


class MsgTuiApp(App):
    """Top-level Textual application."""

    TITLE = "msgTUI"
    CSS = """
    Screen {
        background: $surface;
    }
    """

    # ── Custom messages ───────────────────────────────────────────────────────

    class LoginSuccess(Message):
        def __init__(self, access_token: str, refresh_token: str, user: dict) -> None:
            super().__init__()
            self.access_token  = access_token
            self.refresh_token = refresh_token
            self.user          = user

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.push_screen(AuthScreen())

    def on_msg_tui_app_login_success(self, event: LoginSuccess) -> None:
        self.push_screen(ChatScreen(
            access_token=event.access_token,
            refresh_token=event.refresh_token,
            user=event.user,
        ))

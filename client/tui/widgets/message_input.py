"""Message input bar with typing indicator support."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input


class MessageInput(Widget):
    """Input bar at the bottom of the chat area."""

    CSS = """
    MessageInput {
        height: 3;
        border-top: solid $primary-darken-2;
        padding: 0;
    }
    #msg-input-container {
        height: 3;
    }
    #msg-input {
        width: 1fr;
        height: 3;
        border: none;
    }
    #send-btn {
        width: 10;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("ctrl+enter", "send", "Send"),
        Binding("enter",      "send", "Send"),
    ]

    class SendMessage(Message):
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TypingStarted(Message):
        pass

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="msg-input-container"):
            yield Input(
                placeholder="Type a message… (Enter to send)",
                id="msg-input",
                disabled=True,
            )
            yield Button("Send", variant="primary", id="send-btn", disabled=True)

    def enable(self) -> None:
        self._enabled = True
        self.query_one("#msg-input", Input).disabled = False
        self.query_one("#send-btn", Button).disabled = False
        self.query_one("#msg-input", Input).focus()

    def disable(self) -> None:
        self._enabled = False
        self.query_one("#msg-input", Input).disabled = True
        self.query_one("#send-btn", Button).disabled = True

    def action_send(self) -> None:
        if not self._enabled:
            return
        inp  = self.query_one("#msg-input", Input)
        text = inp.value.strip()
        if text:
            self.post_message(MessageInput.SendMessage(text))
            inp.value = ""

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self.action_send()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "msg-input" and event.value:
            self.post_message(MessageInput.TypingStarted())

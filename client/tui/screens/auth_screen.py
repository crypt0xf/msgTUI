"""Login and registration screen."""
from __future__ import annotations
import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static, TabbedContent, TabPane

from client.api_client import ApiError, api, api_call
from client.key_store import key_store


class AuthScreen(Screen):
    """First screen shown to the user — login or register."""

    CSS = """
    AuthScreen {
        align: center middle;
    }
    #auth-box {
        width: 60;
        height: auto;
        border: round $primary;
        padding: 1 2;
    }
    #title {
        text-align: center;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    .field-label {
        margin-top: 1;
        color: $text-muted;
    }
    Input {
        margin-top: 0;
    }
    Button {
        width: 100%;
        margin-top: 1;
    }
    #error-msg {
        color: $error;
        text-align: center;
        margin-top: 1;
        height: auto;
    }
    #status-msg {
        color: $success;
        text-align: center;
        margin-top: 1;
        height: auto;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="auth-box"):
            yield Static("msgTUI — Secure Messaging", id="title")
            with TabbedContent():
                with TabPane("Login", id="tab-login"):
                    yield Label("Username", classes="field-label")
                    yield Input(placeholder="your_username", id="login-username")
                    yield Label("Password", classes="field-label")
                    yield Input(placeholder="••••••••••••", password=True, id="login-password")
                    yield Label("MFA code (optional)", classes="field-label")
                    yield Input(placeholder="123456", id="login-mfa", max_length=6)
                    yield Button("Login", variant="primary", id="btn-login")

                with TabPane("Register", id="tab-register"):
                    yield Label("Username", classes="field-label")
                    yield Input(placeholder="alice", id="reg-username")
                    yield Label("Email", classes="field-label")
                    yield Input(placeholder="alice@example.com", id="reg-email")
                    yield Label("Password (12+ chars, upper, digit, special)", classes="field-label")
                    yield Input(placeholder="••••••••••••", password=True, id="reg-password")
                    yield Label("Confirm Password", classes="field-label")
                    yield Input(placeholder="••••••••••••", password=True, id="reg-confirm")
                    yield Button("Create Account", variant="success", id="btn-register")

            yield Static("", id="error-msg")
            yield Static("", id="status-msg")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-login":
            self.run_worker(self._do_login(), exclusive=True)
        elif event.button.id == "btn-register":
            self.run_worker(self._do_register(), exclusive=True)

    def _set_error(self, msg: str) -> None:
        self.query_one("#error-msg", Static).update(f"[red]{msg}[/]")
        self.query_one("#status-msg", Static).update("")

    def _set_status(self, msg: str) -> None:
        self.query_one("#status-msg", Static).update(f"[green]{msg}[/]")
        self.query_one("#error-msg", Static).update("")

    async def _do_login(self) -> None:
        username = self.query_one("#login-username", Input).value.strip()
        password = self.query_one("#login-password", Input).value
        totp     = self.query_one("#login-mfa", Input).value.strip()

        if not username or not password:
            self._set_error("Username and password are required")
            return

        self._set_status("Authenticating…")
        try:
            async with api_call() as c:
                data = await c.login(username, password, totp_code=totp)
                me = await c.get_me()
            api.access_token  = data["access_token"]
            api.refresh_token = data["refresh_token"]

            # Load or create key store
            if key_store.has_key_file():
                ok = key_store.load(password)
                if not ok:
                    self._set_error("Failed to unlock key store — wrong password?")
                    return
            else:
                # First login on this device — generate new keys
                # (Server already has keys from registration; this covers the case
                #  where the key file was deleted but the account exists)
                key_store.init_new(password, me["id"])

            self._set_status("Success! Loading…")
            # Pass tokens to main app
            self.app.post_message(
                self.app.LoginSuccess(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                    user=me,
                )
            )
        except ApiError as e:
            self._set_error(f"Login failed: {e.detail}")
        except Exception as e:
            self._set_error(f"Error: {e}")

    async def _do_register(self) -> None:
        username = self.query_one("#reg-username", Input).value.strip()
        email    = self.query_one("#reg-email", Input).value.strip()
        password = self.query_one("#reg-password", Input).value
        confirm  = self.query_one("#reg-confirm", Input).value

        if not all([username, email, password, confirm]):
            self._set_error("All fields are required")
            return
        if password != confirm:
            self._set_error("Passwords do not match")
            return

        self._set_status("Generating keys…")
        from client import crypto
        exc_priv, exc_pub = crypto.generate_exchange_keypair()
        sig_priv, sig_pub = crypto.generate_sign_keypair()
        pub_exc = crypto.pub_to_b64(exc_pub)
        pub_sig = crypto.pub_to_b64(sig_pub)

        self._set_status("Registering…")
        try:
            async with api_call() as c:
                data = await c.register(username, email, password, pub_exc, pub_sig)
                me   = await c.get_me()
            api.access_token  = data["access_token"]
            api.refresh_token = data["refresh_token"]

            # Initialize key store with the freshly generated keys
            key_store.init_new(password, me["id"])
            # Overwrite with the actual keys we generated before registration
            import base64
            key_store._data["exc_priv"] = base64.b64encode(crypto.priv_to_bytes(exc_priv)).decode()
            key_store._data["exc_pub"]  = pub_exc
            key_store._data["sig_priv"] = base64.b64encode(crypto.priv_to_bytes(sig_priv)).decode()
            key_store._data["sig_pub"]  = pub_sig
            key_store._save()

            self._set_status("Account created! Logging in…")
            self.app.post_message(
                self.app.LoginSuccess(
                    access_token=data["access_token"],
                    refresh_token=data["refresh_token"],
                    user=me,
                )
            )
        except ApiError as e:
            self._set_error(f"Registration failed: {e.detail}")
        except Exception as e:
            self._set_error(f"Error: {e}")

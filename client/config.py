"""Client configuration — reads config.toml, then env vars, then CLI args."""
from __future__ import annotations
import os
import sys
from functools import lru_cache
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


def _load_toml() -> dict:
    root = Path(__file__).parent.parent / "config.toml"
    if root.exists():
        with root.open("rb") as f:
            return tomllib.load(f)
    return {}


# Can be overridden before get_settings() is called (e.g. from CLI args)
_server_url_override: str = ""
_ws_url_override: str = ""


def set_server_url(url: str) -> None:
    """Override server URL at runtime (used by --server CLI arg)."""
    global _server_url_override, _ws_url_override
    _server_url_override = url.rstrip("/")
    _ws_url_override = _server_url_override.replace("https://", "wss://").replace("http://", "ws://")
    # Clear cache so next call to get_settings() picks up the new value
    get_settings.cache_clear()


@lru_cache(maxsize=1)
def get_settings() -> "ClientSettings":
    return ClientSettings()


class ClientSettings:
    def __init__(self) -> None:
        raw = _load_toml()
        cli = raw.get("client", {})
        ui  = cli.get("ui", {})

        default_server = cli.get("server_url", "http://127.0.0.1:8765")
        default_ws     = cli.get("ws_url",     "ws://127.0.0.1:8765")

        # Priority: runtime override > env var > config.toml
        raw_url = (
            _server_url_override or
            os.environ.get("MSGTUI_SERVER_URL", "") or
            default_server
        )
        self.server_url: str = raw_url.rstrip("/")
        self.ws_url: str = (
            _ws_url_override or
            os.environ.get("MSGTUI_WS_URL", "") or
            self.server_url.replace("https://", "wss://").replace("http://", "ws://")
        ).rstrip("/")
        self.key_store:  Path = Path(cli.get("key_store", "~/.msgtui/keys.enc")).expanduser()
        self.theme:      str  = ui.get("theme", "dark")
        self.timestamps: bool = ui.get("timestamps", True)
        self.notifications: bool = ui.get("notifications", True)

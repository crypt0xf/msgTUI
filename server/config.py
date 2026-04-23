"""Server configuration — reads config.toml with env-var overrides."""
from __future__ import annotations
import os
import sys
from pathlib import Path
from functools import lru_cache

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


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


def _env(key: str, fallback: str = "") -> str:
    return os.environ.get(key, fallback)


class Settings:
    def __init__(self) -> None:
        raw = _load_toml()
        srv  = raw.get("server", {})
        auth = srv.get("auth", {})
        log  = srv.get("logging", {})

        # Env vars take priority over config.toml
        self.host: str = _env("MSGTUI_HOST") or srv.get("host", "127.0.0.1")
        self.port: int = int(_env("MSGTUI_PORT") or srv.get("port", 8765))
        self.db_url: str = (
            _env("MSGTUI_DB_URL") or
            _env("DATABASE_URL") or
            srv.get("db_url", "sqlite+aiosqlite:///./msgtui.db")
        )
        self.tls_enabled: bool = srv.get("tls_enabled", False)
        self.tls_cert: str = srv.get("tls_cert", "")
        self.tls_key:  str = srv.get("tls_key", "")

        self.jwt_secret: str = _env("MSGTUI_JWT_SECRET") or auth.get("secret_key", "CHANGE_ME")
        self.access_token_ttl:  int = int(auth.get("access_token_ttl_minutes", 30))
        self.refresh_token_ttl: int = int(auth.get("refresh_token_ttl_days", 30))
        self.max_login_attempts: int = int(auth.get("max_login_attempts", 5))
        self.lockout_minutes:    int = int(auth.get("lockout_minutes", 15))

        self.log_level: str = _env("MSGTUI_LOG_LEVEL") or log.get("level", "INFO")
        # Em container, MSGTUI_LOG_FILE="" desativa log em arquivo (só stdout)
        self.log_file:  str = _env("MSGTUI_LOG_FILE") if "MSGTUI_LOG_FILE" in os.environ else log.get("file", "server.log")

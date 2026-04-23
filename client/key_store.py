"""
Encrypted local key store.

Keys are stored in a JSON file encrypted with AES-256-GCM.
The encryption key is derived from the user's password via Argon2id.
The file contains: salt(16) || nonce(12) || ciphertext.
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
from typing import Optional

from argon2.low_level import hash_secret_raw, Type
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from client.config import get_settings
from client import crypto as _crypto


_ARGON2_TIME_COST   = 3
_ARGON2_MEMORY_COST = 65536
_ARGON2_PARALLELISM = 2
_ARGON2_HASH_LEN    = 32


def _derive_key(password: str, salt: bytes) -> bytes:
    return hash_secret_raw(
        secret=password.encode(),
        salt=salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_ARGON2_HASH_LEN,
        type=Type.ID,
    )


class KeyStore:
    """In-memory key store backed by an encrypted file."""

    def __init__(self) -> None:
        self._data: dict = {}
        self._password: str = ""
        self._path: Path = get_settings().key_store

    @property
    def is_loaded(self) -> bool:
        return bool(self._data)

    def init_new(self, password: str, user_id: str) -> None:
        """Generate fresh key pairs and save."""
        exc_priv, exc_pub = _crypto.generate_exchange_keypair()
        sig_priv, sig_pub = _crypto.generate_sign_keypair()

        self._data = {
            "user_id": user_id,
            "exc_priv": base64.b64encode(_crypto.priv_to_bytes(exc_priv)).decode(),
            "exc_pub":  _crypto.pub_to_b64(exc_pub),
            "sig_priv": base64.b64encode(_crypto.priv_to_bytes(sig_priv)).decode(),
            "sig_pub":  _crypto.pub_to_b64(sig_pub),
            "group_keys": {},  # group_id -> base64 group key
        }
        self._password = password
        self._save()

    def load(self, password: str) -> bool:
        """Load from disk. Returns False if wrong password or file not found."""
        if not self._path.exists():
            return False
        try:
            raw = self._path.read_bytes()
            salt       = raw[:16]
            nonce      = raw[16:28]
            ciphertext = raw[28:]
            key  = _derive_key(password, salt)
            aead = AESGCM(key)
            plaintext = aead.decrypt(nonce, ciphertext, None)
            self._data = json.loads(plaintext)
            self._password = password
            return True
        except Exception:
            return False

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        salt  = os.urandom(16)
        nonce = os.urandom(12)
        key   = _derive_key(self._password, salt)
        aead  = AESGCM(key)
        ct    = aead.encrypt(nonce, json.dumps(self._data).encode(), None)
        self._path.write_bytes(salt + nonce + ct)
        self._path.chmod(0o600)

    # ── Key accessors ─────────────────────────────────────────────────────────

    @property
    def user_id(self) -> str:
        return self._data["user_id"]

    def get_exc_priv(self):
        return _crypto.x25519_priv_from_bytes(base64.b64decode(self._data["exc_priv"]))

    def get_exc_pub_b64(self) -> str:
        return self._data["exc_pub"]

    def get_sig_priv(self):
        return _crypto.ed25519_priv_from_bytes(base64.b64decode(self._data["sig_priv"]))

    def get_sig_pub_b64(self) -> str:
        return self._data["sig_pub"]

    # ── Group keys ────────────────────────────────────────────────────────────

    def save_group_key(self, group_id: str, key_bytes: bytes) -> None:
        self._data.setdefault("group_keys", {})[group_id] = base64.b64encode(key_bytes).decode()
        self._save()

    def get_group_key(self, group_id: str) -> Optional[bytes]:
        b64 = self._data.get("group_keys", {}).get(group_id)
        return base64.b64decode(b64) if b64 else None

    def has_key_file(self) -> bool:
        return self._path.exists()


key_store = KeyStore()

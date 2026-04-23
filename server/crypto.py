"""
Server-side crypto helpers.
The server NEVER decrypts messages — it only validates that signatures are
structurally present and that base64 fields are well-formed.
"""
from __future__ import annotations
import base64


def b64_valid(value: str) -> bool:
    """Returns True if value is valid standard or URL-safe base64."""
    try:
        base64.b64decode(value, validate=True)
        return True
    except Exception:
        pass
    try:
        base64.urlsafe_b64decode(value + "==")
        return True
    except Exception:
        return False


def validate_e2ee_fields(ciphertext: str, nonce: str, ephemeral_pub: str, signature: str) -> bool:
    return all(b64_valid(f) for f in [ciphertext, nonce, ephemeral_pub, signature])

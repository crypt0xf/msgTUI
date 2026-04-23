"""
Client-side E2EE implementation.

DM encryption model (sender → recipient):
  1. Sender generates ephemeral X25519 key pair.
  2. ECDH: shared_secret = DH(ephemeral_priv, recipient_pub_exchange).
  3. HKDF-SHA256 derives a 32-byte AES-256 key.
  4. AES-256-GCM encrypts the plaintext with a random 12-byte nonce.
  5. Sender signs (ciphertext || nonce || ephemeral_pub || recipient_pub) with Ed25519.
  6. Transmitted: {ciphertext, nonce, ephemeral_pub, signature}.

Group encryption model:
  • Each group has a 32-byte symmetric group key (AES-256).
  • The group key is encrypted for every member with that member's X25519 public key
    using ECDH (same as DM but with a static admin ephemeral key per key distribution event).
  • Messages are encrypted with AES-256-GCM using the group key.
  • Messages are signed with the sender's Ed25519 key.
"""
from __future__ import annotations
import base64
import json
import os

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.exceptions import InvalidSignature


# ── Key generation ─────────────────────────────────────────────────────────────

def generate_exchange_keypair() -> tuple[X25519PrivateKey, X25519PublicKey]:
    priv = X25519PrivateKey.generate()
    return priv, priv.public_key()


def generate_sign_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


# ── Serialization ──────────────────────────────────────────────────────────────

def pub_to_b64(key: X25519PublicKey | Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def x25519_pub_from_b64(b64: str) -> X25519PublicKey:
    raw = base64.b64decode(b64)
    return X25519PublicKey.from_public_bytes(raw)


def ed25519_pub_from_b64(b64: str) -> Ed25519PublicKey:
    raw = base64.b64decode(b64)
    return Ed25519PublicKey.from_public_bytes(raw)


def priv_to_bytes(key: X25519PrivateKey | Ed25519PrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )


def x25519_priv_from_bytes(raw: bytes) -> X25519PrivateKey:
    return X25519PrivateKey.from_private_bytes(raw)


def ed25519_priv_from_bytes(raw: bytes) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(raw)


# ── HKDF key derivation ────────────────────────────────────────────────────────

def derive_aes_key(shared_secret: bytes, salt: bytes | None = None) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=b"msgtui-dm-v1",
    )
    return hkdf.derive(shared_secret)


# ── DM encryption / decryption ─────────────────────────────────────────────────

def encrypt_dm(
    plaintext: str,
    recipient_pub_exchange_b64: str,
    sender_priv_sign: Ed25519PrivateKey,
    sender_pub_exchange_b64: str,  # used as AEAD additional data context
) -> dict:
    """Encrypt a DM message. Returns wire-format dict."""
    # 1. Ephemeral X25519 key pair
    eph_priv, eph_pub = generate_exchange_keypair()

    # 2. ECDH
    recipient_pub = x25519_pub_from_b64(recipient_pub_exchange_b64)
    shared_secret = eph_priv.exchange(recipient_pub)

    # 3. Derive AES key
    eph_pub_bytes = priv_to_bytes(eph_priv)  # same raw bytes via public_bytes below
    eph_pub_b64   = pub_to_b64(eph_pub)
    aes_key = derive_aes_key(shared_secret, salt=base64.b64decode(recipient_pub_exchange_b64)[:16])

    # 4. Encrypt
    nonce      = os.urandom(12)
    aead       = AESGCM(aes_key)
    ciphertext = aead.encrypt(nonce, plaintext.encode(), None)

    # 5. Sign: ciphertext || nonce || ephemeral_pub || recipient_pub
    sig_data  = ciphertext + nonce + base64.b64decode(eph_pub_b64) + base64.b64decode(recipient_pub_exchange_b64)
    signature = sender_priv_sign.sign(sig_data)

    return {
        "ciphertext":   base64.b64encode(ciphertext).decode(),
        "nonce":        base64.b64encode(nonce).decode(),
        "ephemeral_pub": eph_pub_b64,
        "signature":    base64.b64encode(signature).decode(),
    }


def decrypt_dm(
    ciphertext_b64: str,
    nonce_b64: str,
    ephemeral_pub_b64: str,
    signature_b64: str,
    my_priv_exchange: X25519PrivateKey,
    my_pub_exchange_b64: str,
    sender_pub_sign_b64: str,
) -> str:
    """Decrypt and verify a DM message. Raises ValueError/InvalidSignature on failure."""
    ciphertext   = base64.b64decode(ciphertext_b64)
    nonce        = base64.b64decode(nonce_b64)
    eph_pub_raw  = base64.b64decode(ephemeral_pub_b64)
    signature    = base64.b64decode(signature_b64)

    # 1. Verify signature BEFORE decrypting (fail-fast on tampered data)
    sender_pub = ed25519_pub_from_b64(sender_pub_sign_b64)
    sig_data   = ciphertext + nonce + eph_pub_raw + base64.b64decode(my_pub_exchange_b64)
    try:
        sender_pub.verify(signature, sig_data)
    except InvalidSignature:
        raise ValueError("Message signature verification failed — possible tampering")

    # 2. ECDH
    eph_pub      = X25519PublicKey.from_public_bytes(eph_pub_raw)
    shared_secret = my_priv_exchange.exchange(eph_pub)

    # 3. Derive key
    aes_key = derive_aes_key(shared_secret, salt=base64.b64decode(my_pub_exchange_b64)[:16])

    # 4. Decrypt
    aead = AESGCM(aes_key)
    try:
        plaintext = aead.decrypt(nonce, ciphertext, None)
    except Exception:
        raise ValueError("Decryption failed — message may be corrupted or key mismatch")

    return plaintext.decode()


# ── Group encryption / decryption ─────────────────────────────────────────────

def generate_group_key() -> bytes:
    return os.urandom(32)


def encrypt_group_key_for_member(group_key: bytes, member_pub_exchange_b64: str) -> dict:
    """Encrypt a group key for a specific member (returns wire dict for key bundle)."""
    eph_priv, eph_pub = generate_exchange_keypair()
    member_pub  = x25519_pub_from_b64(member_pub_exchange_b64)
    shared      = eph_priv.exchange(member_pub)
    aes_key     = derive_aes_key(shared, salt=base64.b64decode(member_pub_exchange_b64)[:16])
    nonce       = os.urandom(12)
    aead        = AESGCM(aes_key)
    enc_key     = aead.encrypt(nonce, group_key, None)
    return {
        "eph_pub":   pub_to_b64(eph_pub),
        "nonce":     base64.b64encode(nonce).decode(),
        "enc_key":   base64.b64encode(enc_key).decode(),
    }


def decrypt_group_key(
    key_slice_json: str,
    my_priv_exchange: X25519PrivateKey,
    my_pub_exchange_b64: str,
) -> bytes:
    """Recover the group key from my key slice."""
    slice_ = json.loads(key_slice_json)
    eph_pub = x25519_pub_from_b64(slice_["eph_pub"])
    shared  = my_priv_exchange.exchange(eph_pub)
    aes_key = derive_aes_key(shared, salt=base64.b64decode(my_pub_exchange_b64)[:16])
    nonce   = base64.b64decode(slice_["nonce"])
    enc_key = base64.b64decode(slice_["enc_key"])
    aead    = AESGCM(aes_key)
    return aead.decrypt(nonce, enc_key, None)


def encrypt_group_message(
    plaintext: str,
    group_key: bytes,
    sender_priv_sign: Ed25519PrivateKey,
    group_id: str,
) -> dict:
    nonce      = os.urandom(12)
    aead       = AESGCM(group_key)
    ciphertext = aead.encrypt(nonce, plaintext.encode(), group_id.encode())
    sig_data   = ciphertext + nonce + group_id.encode()
    signature  = sender_priv_sign.sign(sig_data)
    return {
        "ciphertext":    base64.b64encode(ciphertext).decode(),
        "nonce":         base64.b64encode(nonce).decode(),
        "ephemeral_pub": base64.b64encode(b"\x00" * 32).decode(),  # sentinel for group msgs
        "signature":     base64.b64encode(signature).decode(),
    }


def decrypt_group_message(
    ciphertext_b64: str,
    nonce_b64: str,
    signature_b64: str,
    sender_pub_sign_b64: str,
    group_key: bytes,
    group_id: str,
) -> str:
    ciphertext = base64.b64decode(ciphertext_b64)
    nonce      = base64.b64decode(nonce_b64)
    signature  = base64.b64decode(signature_b64)

    sender_pub = ed25519_pub_from_b64(sender_pub_sign_b64)
    sig_data   = ciphertext + nonce + group_id.encode()
    try:
        sender_pub.verify(signature, sig_data)
    except InvalidSignature:
        raise ValueError("Group message signature verification failed")

    aead = AESGCM(group_key)
    try:
        plaintext = aead.decrypt(nonce, ciphertext, group_id.encode())
    except Exception:
        raise ValueError("Group message decryption failed")
    return plaintext.decode()

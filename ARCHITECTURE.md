# msgTUI — Architecture & Technical Documentation

## Technology Choices

| Layer       | Technology            | Justification |
|-------------|----------------------|---------------|
| Language    | Python 3.11+         | Mature async ecosystem, rich cryptography libraries, fast prototyping without sacrificing security |
| Server      | FastAPI + uvicorn    | Async-first, type-safe, WebSocket support, auto-generated OpenAPI docs |
| Database    | SQLAlchemy async + SQLite (dev) / PostgreSQL (prod) | Schema migrations, async driver, no extra process for dev |
| TUI         | Textual              | Modern, CSS-styled, async-native terminal UI |
| Crypto      | `cryptography` lib   | Audited by PyCA, uses libsodium/OpenSSL backends, well-maintained |
| Passwords   | argon2-cffi (Argon2id) | Winner of Password Hashing Competition, memory-hard, side-channel resistant |
| Auth tokens | PyJWT (HS256)        | Stateless access tokens; refresh tokens stored hashed in DB |
| MFA         | pyotp (TOTP/RFC 6238) | Standard, compatible with any authenticator app |

---

## Directory Structure

```
msgTUI/
├── config.toml              # Unified configuration
├── requirements.txt
├── run_server.py            # Server entry point
├── run_client.py            # Client entry point
│
├── shared/
│   └── protocol.py          # Wire-protocol message types (WsEnvelope, E2EEPayload)
│
├── server/
│   ├── main.py              # FastAPI app factory
│   ├── config.py            # Settings loader
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models.py            # ORM models (User, Session, Conversation, Message, Group, GroupMember)
│   ├── schemas.py           # Pydantic request/response schemas
│   ├── auth.py              # Argon2 hashing, JWT creation/validation, lockout logic
│   ├── crypto.py            # Server-side: E2EE field validation only
│   ├── websocket_manager.py # In-memory connection registry + broadcast
│   └── routes/
│       ├── auth.py          # /auth/* endpoints
│       ├── users.py         # /users/* endpoints
│       ├── messages.py      # /messages/* endpoints
│       ├── groups.py        # /groups/* endpoints
│       └── websocket.py     # WebSocket /ws endpoint
│
└── client/
    ├── config.py            # Client settings loader
    ├── crypto.py            # Full E2EE: X25519, Ed25519, AES-256-GCM, HKDF
    ├── key_store.py         # Encrypted local key storage (Argon2id + AES-256-GCM)
    ├── api_client.py        # Async HTTP client (httpx)
    ├── ws_client.py         # WebSocket client with auto-reconnect
    └── tui/
        ├── app.py           # Root Textual App
        ├── screens/
        │   ├── auth_screen.py   # Login + Register UI
        │   └── chat_screen.py   # Main messaging UI
        └── widgets/
            ├── sidebar.py       # Contact/group list + search
            ├── message_list.py  # Scrollable decrypted message view
            └── message_input.py # Input bar
```

---

## Authentication Flow

```
Client                                    Server
  │                                          │
  │── POST /auth/register ─────────────────► │
  │   {username, email, password,            │
  │    pub_key_exchange, pub_key_sign}        │
  │                                          │── Argon2id hash password
  │                                          │── Store user + public keys
  │◄── {access_token, refresh_token} ───────│
  │                                          │
  │── POST /auth/login ──────────────────── ►│
  │   {username, password, [totp_code]}      │── Verify Argon2id hash
  │                                          │── Check lockout (5 failures → 15 min)
  │                                          │── Verify TOTP if MFA enabled
  │◄── {access_token, refresh_token} ───────│
  │                                          │
  │── GET /ws  (WebSocket upgrade) ─────────►│
  │── {"type":"auth","payload":{"token":…}}  │── JWT decode + validate
  │◄── {"type":"authenticated"} ────────────│
```

### Token Design
- **Access token**: Short-lived JWT (30 min), HS256 signed. Contains `sub` (user_id), `sid` (session_id), `type: "access"`.
- **Refresh token**: Long-lived JWT (30 days), stored as SHA-256 hash in DB. Single-use (rotated on each refresh).
- **Session revocation**: Invalidating a refresh token hash immediately kills the session.

---

## End-to-End Encryption

### DM Message Flow

```
Alice (sender)                              Server                     Bob (recipient)
  │                                           │                             │
  │ 1. Generate ephemeral X25519 keypair      │                             │
  │ 2. Fetch Bob's pub_key_exchange ─────────►│                             │
  │◄── {pub_key_exchange: B_pub} ────────────│                             │
  │                                           │                             │
  │ 3. ECDH: shared = DH(A_eph_priv, B_pub)  │                             │
  │ 4. HKDF-SHA256(shared, salt=B_pub[:16])  │                             │
  │    → AES-256 key                          │                             │
  │ 5. AES-256-GCM encrypt(plaintext)         │                             │
  │    → {ciphertext, nonce}                  │                             │
  │ 6. Ed25519 sign(ct||nonce||A_eph_pub||B_pub) │                         │
  │    → signature                            │                             │
  │                                           │                             │
  │── POST /messages/{conv_id}/send ─────────►│                             │
  │   {ciphertext, nonce, ephemeral_pub,      │── Store encrypted blob      │
  │    signature, sender_id, message_id}      │── Validate base64 fields    │
  │                                           │── Forward via WebSocket ───►│
  │                                           │                             │
  │                                           │            Bob decrypts:    │
  │                                           │  1. Ed25519 verify(sig)     │
  │                                           │  2. ECDH(B_priv, A_eph_pub)│
  │                                           │  3. HKDF → AES key          │
  │                                           │  4. AES-256-GCM decrypt     │
```

**Key property**: The server stores and forwards only ciphertext. It cannot read messages.

### Group Message Flow

1. Group creator generates a random 32-byte **group key** (AES-256).
2. For each member: encrypt the group key using ECDH (same as DM) → `{eph_pub, nonce, enc_key}`.
3. Store the entire key bundle `{user_id: encrypted_slice, …}` on the server.
4. Each member fetches their slice, decrypts it to recover the group key.
5. Messages are encrypted with `AES-256-GCM(group_key, plaintext, aad=group_id)`.
6. Messages are signed with the sender's Ed25519 private key.
7. On member removal, the admin must generate a **new group key** and re-distribute (forward secrecy).

### Local Key Store

Private keys are stored in `~/.msgtui/keys.enc`:
```
[16-byte Argon2id salt] [12-byte AES-GCM nonce] [AES-256-GCM ciphertext of JSON payload]
```
The file is encrypted using Argon2id (time=3, mem=64 MB, p=2) to derive the AES-256 key from the user's login password. File permissions are set to `0600`.

---

## Data Model

```
User
  id, username (unique), email (unique)
  password_hash (Argon2id)
  pub_key_exchange (X25519, base64)
  pub_key_sign (Ed25519, base64)
  mfa_secret, mfa_enabled
  failed_logins, locked_until

Session
  id (= JWT sid), user_id → User
  token_hash (SHA-256 of refresh token)
  device_name, expires_at, is_revoked

Conversation (DM pair)
  id, user_a_id, user_b_id (sorted, unique pair)

Message
  id, conversation_id OR group_id, sender_id
  ciphertext, nonce, ephemeral_pub, signature  ← E2EE fields only
  timestamp, delivered, read, read_at

Group
  id, name, creator_id
  key_bundle (JSON: {user_id: encrypted_key_slice})

GroupMember
  group_id, user_id, role (admin|member)
```

---

## Security Measures

| Threat | Mitigation |
|--------|-----------|
| Weak passwords | Argon2id (memory-hard), strength validation (12+ chars, upper, digit, symbol) |
| Brute force login | Max 5 attempts, then 15-min lockout |
| Token theft | Short-lived access tokens (30 min), refresh token rotation, DB-side revocation |
| MITM | TLS in production (config `tls_enabled = true`); WebSocket over WSS |
| Message eavesdropping | E2EE: server sees only ciphertext |
| Message tampering | Ed25519 signatures verified client-side before decryption |
| Replay attacks | Each message has a unique `message_id` (UUID v4) + GCM authentication tag |
| Key compromise | Ephemeral DH key per message (forward secrecy for DMs) |
| SQL injection | SQLAlchemy ORM with parameterized queries only |
| XSS / injection | Pydantic input validation + Textual renders as text, not HTML |
| Privilege escalation | Least privilege: group operations check membership/role per request |
| Local key exposure | Key file encrypted at rest, never stored plaintext, `chmod 0600` |
| Audit trail | Structured server logs (no message content, user IDs only) |

---

## Network Protocol

### REST API (HTTP/1.1 or HTTP/2)
Base URL: `http[s]://<host>:<port>`

| Method | Path | Description |
|--------|------|-------------|
| POST | /auth/register | Create account |
| POST | /auth/login | Authenticate |
| POST | /auth/refresh | Rotate tokens |
| POST | /auth/logout | Revoke session |
| POST | /auth/mfa/setup | Configure TOTP |
| POST | /auth/mfa/enable | Activate MFA |
| GET  | /users/me | Current user profile |
| GET  | /users/search?q= | Find users |
| GET  | /users/{id}/key-bundle | E2EE public keys |
| GET  | /messages/conversations | List DMs |
| POST | /messages/conversations/{peer_id} | Open/get DM |
| POST | /messages/conversations/{id}/history | Load messages |
| POST | /messages/conversations/{id}/send | Send DM |
| POST | /messages/conversations/{id}/read/{msg_id} | Mark read |
| GET  | /groups | List groups |
| POST | /groups | Create group |
| GET  | /groups/{id} | Group details |
| POST | /groups/{id}/members | Add member |
| DELETE | /groups/{id}/members/{uid} | Remove member |
| GET  | /groups/{id}/key-bundle | Fetch key slice |
| PUT  | /groups/{id}/key-bundle | Update key bundle |
| POST | /groups/{id}/history | Group history |
| POST | /groups/{id}/send | Send group message |
| GET  | /health | Health check |

### WebSocket (`/ws`)
All frames are UTF-8 JSON matching `WsEnvelope {type, payload}`.

**Client → Server**
| type | payload | description |
|------|---------|-------------|
| auth | {token} | Must be first frame |
| typing | {peer_id} or {group_id} | Typing indicator |
| read_ack | {message_id} | Mark message read |
| ping | {} | Keep-alive |

**Server → Client**
| type | payload | description |
|------|---------|-------------|
| authenticated | {user_id} | Auth confirmed |
| message | E2EE payload | Incoming DM |
| group_message | E2EE payload | Incoming group message |
| delivery_ack | {message_id} | Message delivered |
| read_receipt | {message_id, reader_id} | Read confirmation |
| typing_indicator | {sender_id, peer_id/group_id} | Typing notification |
| user_status | {user_id, status} | Online/offline |
| pong | {ts} | Keep-alive response |
| error | {code, message} | Error notification |

---

## Running in Production

1. **Generate a strong JWT secret**:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   export MSGTUI_JWT_SECRET="<output>"
   ```

2. **Enable TLS** — obtain a certificate (Let's Encrypt):
   ```toml
   [server]
   tls_enabled = true
   tls_cert = "/etc/letsencrypt/live/example.com/fullchain.pem"
   tls_key  = "/etc/letsencrypt/live/example.com/privkey.pem"
   ```

3. **Switch to PostgreSQL**:
   ```toml
   [server]
   db_url = "postgresql+asyncpg://user:pass@localhost/msgtui"
   ```
   Run `alembic` migrations for schema management.

4. **Run behind a reverse proxy** (nginx/Caddy) for additional TLS termination and rate limiting.

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start server (terminal 1)
python run_server.py

# 3. Start client (terminal 2)
python run_client.py
```

The server starts on `http://127.0.0.1:8765`. The client connects automatically. API docs available at `http://127.0.0.1:8765/docs`.

### Keyboard Shortcuts (Client TUI)

| Shortcut | Action |
|----------|--------|
| Tab | Navigate between panels |
| Enter | Send message |
| Ctrl+N | New DM |
| Ctrl+G | New group |
| Ctrl+L | Focus sidebar |
| Ctrl+S | Search messages |
| Escape | Focus input bar |
| Ctrl+Q | Quit |

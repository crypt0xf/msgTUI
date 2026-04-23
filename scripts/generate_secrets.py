#!/usr/bin/env python3
"""
Gera os segredos necessários e cria o arquivo .env automaticamente.
Execute UMA VEZ antes de subir os containers:
    python scripts/generate_secrets.py
"""
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
ENV_FILE = ROOT / ".env"

if ENV_FILE.exists():
    print(f"[!] {ENV_FILE} já existe. Delete-o manualmente para regenerar.")
    sys.exit(1)

db_password = secrets.token_hex(20)   # 40 chars
jwt_secret  = secrets.token_hex(32)   # 64 chars

content = f"""# Gerado automaticamente por scripts/generate_secrets.py
# NUNCA commite este arquivo no git

DB_PASSWORD={db_password}
JWT_SECRET={jwt_secret}
SERVER_PORT=8765
LOG_LEVEL=INFO
"""

ENV_FILE.write_text(content)
ENV_FILE.chmod(0o600)

print("[OK] Arquivo .env criado com sucesso.")
print(f"  DB_PASSWORD : {db_password}")
print(f"  JWT_SECRET  : {jwt_secret[:16]}...  (guarde em local seguro)")
print()
print("Próximo passo:")
print("  docker compose up --build -d")

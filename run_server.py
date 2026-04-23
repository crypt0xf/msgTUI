#!/usr/bin/env python3
"""Start the msgTUI server."""
import sys
import uvicorn
from server.config import get_settings


def main():
    cfg = get_settings()

    ssl_kwargs = {}
    if cfg.tls_enabled and cfg.tls_cert and cfg.tls_key:
        ssl_kwargs = {"ssl_certfile": cfg.tls_cert, "ssl_keyfile": cfg.tls_key}

    uvicorn.run(
        "server.main:app",
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        reload=False,
        **ssl_kwargs,
    )


if __name__ == "__main__":
    main()

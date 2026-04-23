#!/usr/bin/env python3
"""
Start the msgTUI client.

Usage:
    python run_client.py                          # connect to localhost (default)
    python run_client.py --server http://IP:8765  # connect to a specific server
"""
import argparse
import logging
import sys
from pathlib import Path


def _setup_logging() -> None:
    log_file = Path("msgtui_client.log")
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main() -> None:
    _setup_logging()

    parser = argparse.ArgumentParser(description="msgTUI — Secure Terminal Messenger")
    parser.add_argument(
        "--server", "-s",
        metavar="URL",
        help="Server URL, e.g. http://203.0.113.5:8765 or https://meuservidor.com",
        default="",
    )
    args = parser.parse_args()

    if args.server:
        from client.config import set_server_url
        set_server_url(args.server)

    from client.main import main as run
    run()


if __name__ == "__main__":
    main()

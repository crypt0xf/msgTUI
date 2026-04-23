"""FastAPI application factory and startup/shutdown lifecycle."""
from __future__ import annotations
import logging
import logging.handlers
import sys

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.config import get_settings
from server.database import init_db
from server.routes import auth, groups, messages, users, websocket


def create_app() -> FastAPI:
    cfg = get_settings()
    _configure_logging(cfg)

    app = FastAPI(
        title="msgTUI Server",
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    # Em produção, substitua "*" pela sua origem real
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Lifecycle ──────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        import asyncio
        log = logging.getLogger(__name__)
        # Retry DB connection — PostgreSQL pode demorar alguns segundos para aceitar conexões
        for attempt in range(1, 11):
            try:
                await init_db()
                log.info("msgTUI server started on %s:%d", cfg.host, cfg.port)
                return
            except Exception as exc:
                log.warning("DB not ready (attempt %d/10): %s — aguardando 3s…", attempt, exc)
                await asyncio.sleep(3)
        raise RuntimeError("Banco de dados inacessível após 10 tentativas")

    # ── Global error handlers ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled(_req: Request, exc: Exception):
        logging.getLogger(__name__).exception("Unhandled error: %s", exc)
        return JSONResponse(status_code=500, content={"code": "internal_error", "message": "Internal server error"})

    # ── Routes ─────────────────────────────────────────────────────────────
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(messages.router)
    app.include_router(groups.router)
    app.include_router(websocket.router)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


def _configure_logging(cfg) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    # Só escreve em arquivo se log_file estiver configurado e não vazio
    if cfg.log_file:
        try:
            fh = logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=10_000_000, backupCount=3)
            handlers.append(fh)
        except OSError:
            pass  # sem permissão de escrita (ex: container read-only)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


app = create_app()

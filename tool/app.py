from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import contextlib
import os

from db_cffi_bridge import router as db_cffi_router
from reisevergleich import router as reisevergleich_router
from reisevergleich.config import APP_VERSION
from reisevergleich.history_scheduler import start_scheduler, stop_scheduler
from reisevergleich.price_history import start_watch_loop

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"


def _mcp_app():
    """MCP-Server unter /mcp (nur lesend); mit TRAVIOREL_MCP=0 abschaltbar."""
    if os.environ.get("TRAVIOREL_MCP", "1") == "0":
        return None
    from mcp.server.transport_security import TransportSecuritySettings

    from reisevergleich.mcp_server import mcp

    security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    return mcp, mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, transport_security=security)


_mcp = _mcp_app()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = start_scheduler()
    watcher = start_watch_loop()
    try:
        async with contextlib.AsyncExitStack() as stack:
            if _mcp is not None:
                await stack.enter_async_context(_mcp[1].router.lifespan_context(_mcp[0]))
            yield
    finally:
        if watcher is not None:
            watcher[1].set()
            watcher[0].cancel()
        await stop_scheduler(scheduler)

app = FastAPI(
    title="Traviorel",
    version=APP_VERSION,
    description=(
        "Deterministischer multimodaler Reisevergleich mit DB/db-vendo, Split-Ticket-Prüfung, "
        "Transitous, Flix und trvl."
    ),
    lifespan=lifespan,
)
app.include_router(reisevergleich_router)
if _mcp is not None:
    from starlette.routing import Route

    class _McpEndpoint:
        """Reicht /mcp an den MCP-Server weiter (ohne Weiterleitung auf /mcp/)."""

        async def __call__(self, scope, receive, send):
            await _mcp[1](scope, receive, send)

    app.router.routes.append(Route("/mcp", _McpEndpoint(), methods=["GET", "POST", "DELETE"]))
app.include_router(db_cffi_router)
app.mount("/assets", StaticFiles(directory=UI), name="assets")


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(UI / "index.html")

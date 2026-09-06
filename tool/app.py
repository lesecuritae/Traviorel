from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db_cffi_bridge import router as db_cffi_router
from reisevergleich import router as reisevergleich_router
from reisevergleich.config import APP_VERSION
from reisevergleich.history_scheduler import start_scheduler, stop_scheduler

ROOT = Path(__file__).resolve().parent
UI = ROOT / "ui"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = start_scheduler()
    try:
        yield
    finally:
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

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, select, text

from openproxy.database import async_session_factory, engine
from openproxy.models import Base, Setting, Provider
from openproxy.utils.auth import verify_auth
from openproxy.utils.logging_config import setup_logging


# We import routers so their routes get registered
from openproxy.api import chat as chat_api
from openproxy.api import embeddings as embeddings_api
from openproxy.api import models as models_api
from openproxy.api import admin as admin_api
from openproxy.web import routes as web_routes

logger = logging.getLogger(__name__)

_start_time: float = 0.0

# ---- Watchdog task ----


async def _watchdog_loop() -> None:
    """Periodic health monitor that runs in the background.

    Checks database connectivity and logs a health summary every 60 s.
    Any unexpected error is caught and logged — this is the primary
    diagnostic signal if the app becomes stuck.
    """
    while True:
        try:
            await asyncio.sleep(60)
            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
            uptime = int(time.monotonic() - _start_time)
            logger.info("Watchdog ok", extra={"uptime_seconds": uptime})
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Watchdog health check failed")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create database tables and seed default settings on startup."""
    global _start_time
    setup_logging()
    _start_time = time.monotonic()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed default settings
    async with async_session_factory() as session:
        from openproxy.utils.settings_helper import DEFAULTS

        for key, value in DEFAULTS.items():
            result = await session.execute(select(Setting).where(Setting.key == key))
            if not result.scalar_one_or_none():
                session.add(Setting(key=key, value=value))
        # Remove any settings no longer in DEFAULTS
        await session.execute(
            delete(Setting).where(Setting.key.notin_(list(DEFAULTS.keys())))
        )
        await session.commit()

    # Start the background watchdog
    watchdog_task = asyncio.create_task(_watchdog_loop(), name="watchdog")
    logger.info("Application started")

    yield

    # Clean shutdown
    watchdog_task.cancel()
    try:
        await watchdog_task
    except asyncio.CancelledError:
        pass
    await engine.dispose()
    logger.info("Application stopped")


app = FastAPI(
    title="OpenAIProxy",
    description="LLM router/proxy with priority-based failover",
    version="0.1.0",
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="openproxy/web/templates")


# ---- Authentication middleware ----
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Public paths — no auth required
    public_paths = {"/health", "/login", "/logout"}
    if request.url.path in public_paths or request.url.path.startswith("/static"):
        return await call_next(request)

    from openproxy.config import settings as s

    if not s.auth_token:
        return await call_next(request)

    # Extract token: Authorization header first, then cookie
    token = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()

    if not token:
        token = request.cookies.get("openproxy_token", "")

    if not verify_auth(token):
        if request.url.path.startswith(("/api/", "/v1/")):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {"message": "Unauthorized", "type": "auth_error"}
                },
            )
        return RedirectResponse(url="/login", status_code=303)

    return await call_next(request)


# ---- Request logging middleware (outermost — captures every request) ----
@app.middleware("http")
async def request_log_middleware(request: Request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
        elapsed = int((time.monotonic() - start) * 1000)
        if response.status_code < 400:
            logger.info(
                "Request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": elapsed,
                },
            )
        else:
            logger.warning(
                "Request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "latency_ms": elapsed,
                },
            )
        return response
    except Exception:
        elapsed = int((time.monotonic() - start) * 1000)
        logger.exception(
            "Request unhandled exception",
            extra={
                "method": request.method,
                "path": request.url.path,
                "latency_ms": elapsed,
            },
        )
        raise


# ---- Login / Logout (unauthenticated) ----
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    from openproxy.config import settings as s

    return templates.TemplateResponse(
        request, "login.html", {"error": error, "auth_enabled": bool(s.auth_token)}
    )


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    from openproxy.config import settings as s

    if verify_auth(password) and s.auth_token:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key="openproxy_token",
            value=password,
            httponly=True,
            samesite="strict",
            # secure=True,  # uncomment if using HTTPS
        )
        return response
    return RedirectResponse(url="/login?error=1", status_code=303)


@app.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("openproxy_token", path="/")
    return response


# ---- OpenAI-compatible proxy endpoints ----
app.include_router(chat_api.router)
app.include_router(embeddings_api.router)
app.include_router(models_api.router)

# ---- Admin API ----
app.include_router(admin_api.router)

# ---- Web UI ----
app.include_router(web_routes.router)

# ---- Static files for web UI ----
app.mount("/static", StaticFiles(directory="openproxy/web/static"), name="static")


# ---- Global error handler ----
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "Unhandled exception processing request",
        extra={
            "method": request.method,
            "path": request.url.path,
        },
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": f"Internal server error: {exc}",
                "type": "internal_error",
            }
        },
    )


# ---- Health check ----
@app.get("/health")
async def health():
    db_ok = False
    provider_count = 0
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
            db_ok = True
            # Count active providers
            result = await session.execute(
                select(Provider).where(Provider.is_active.is_(True))
            )
            providers = result.scalars().all()
            provider_count = len(providers)
    except Exception:
        logger.exception("Health check database probe failed")

    uptime = int(time.monotonic() - _start_time) if _start_time else 0

    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "active_providers": provider_count,
        "uptime_seconds": uptime,
    }

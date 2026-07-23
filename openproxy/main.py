from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from openproxy.database import async_session_factory, engine
from openproxy.models import Base, Setting
from openproxy.utils.auth import verify_auth


# We import routers so their routes get registered
from openproxy.api import chat as chat_api
from openproxy.api import embeddings as embeddings_api
from openproxy.api import models as models_api
from openproxy.api import admin as admin_api
from openproxy.web import routes as web_routes


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create database tables and seed default settings on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed default settings
    async with async_session_factory() as session:
        from openproxy.utils.settings_helper import DEFAULTS
        from sqlalchemy import select, delete

        for key, value in DEFAULTS.items():
            result = await session.execute(select(Setting).where(Setting.key == key))
            if not result.scalar_one_or_none():
                session.add(Setting(key=key, value=value))
        # Remove any settings no longer in DEFAULTS
        await session.execute(
            delete(Setting).where(Setting.key.notin_(list(DEFAULTS.keys())))
        )
        await session.commit()
    yield
    await engine.dispose()


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
    return {"status": "ok"}

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from openproxy.database import get_session
from openproxy.models import Provider

router = APIRouter()

templates = Jinja2Templates(directory="openproxy/web/templates")


def _provider_status(provider: Provider) -> str:
    if not provider.is_active:
        return "disabled"
    if provider.cooldown_until and provider.cooldown_until > provider.created_at:  # approximate check
        return "cooldown"
    return "active"


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session=Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
    )


@router.get("/providers", response_class=HTMLResponse)
async def providers_page(request: Request, session=Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="providers.html",
    )


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request, session=Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="logs.html",
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, session=Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
    )


@router.get("/sets", response_class=HTMLResponse)
async def sets_page(request: Request, session=Depends(get_session)):
    return templates.TemplateResponse(
        request=request,
        name="sets.html",
    )

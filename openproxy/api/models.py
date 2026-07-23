from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from openproxy.database import get_session
from openproxy.models import ModelSet, ModelSetEntry, Provider

router = APIRouter()


@router.get("/v1/models")
async def list_models(
    session: AsyncSession = Depends(get_session),
):
    """Return all model sets as available models, each with its effective context window."""
    stmt = (
        select(ModelSet)
        .options(
            selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider).selectinload(Provider.models)
        )
        .order_by(ModelSet.name.asc())
    )
    result = await session.execute(stmt)
    sets = result.scalars().unique().all()

    ts = int(datetime.now().timestamp())
    data = []
    for s in sets:
        # Compute effective context = min of all enabled entries' context sizes
        effective_context = None
        for e in s.entries:
            if not e.is_enabled:
                continue
            if not e.provider or not e.provider.is_active:
                continue
            if e.provider.cooldown_until and e.provider.cooldown_until > datetime.now():
                continue
            # Look up context_size from the provider's model list
            ctx = None
            if e.provider and e.provider.models:
                for pm in e.provider.models:
                    if pm.name == e.model_name and pm.context_size is not None:
                        ctx = pm.context_size
                        break
            if ctx is not None:
                if effective_context is None or ctx < effective_context:
                    effective_context = ctx

        entry = {
            "id": s.name,
            "object": "model",
            "created": ts,
            "owned_by": "openaiproxy",
        }
        if effective_context is not None:
            entry["max_context_length"] = effective_context
        data.append(entry)

    return JSONResponse(content={"object": "list", "data": data})

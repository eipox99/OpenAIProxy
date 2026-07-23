from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.database import get_session
from openproxy.models import ModelSet

router = APIRouter()


@router.get("/v1/models")
async def list_models(
    session: AsyncSession = Depends(get_session),
):
    """Return all model sets as available models."""
    stmt = select(ModelSet).order_by(ModelSet.name.asc())
    result = await session.execute(stmt)
    sets = result.scalars().all()

    now = datetime.now().isoformat()
    ts = int(datetime.now().timestamp())
    data = [
        {
            "id": s.name,
            "object": "model",
            "created": ts,
            "owned_by": "openaiproxy",
        }
        for s in sets
    ]
    return JSONResponse(content={"object": "list", "data": data})

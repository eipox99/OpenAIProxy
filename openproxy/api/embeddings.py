from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.database import get_session
from openproxy.router.proxy import proxy_embedding

router = APIRouter()


@router.post("/v1/embeddings")
async def embeddings(
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    set_name = body.get("model", "")

    if not set_name:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "model (set name) is required",
                    "type": "invalid_request_error",
                }
            },
        )

    try:
        result = await proxy_embedding(session, set_name, body)
        return JSONResponse(content=result)
    except RuntimeError as e:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": str(e),
                    "type": "proxy_error",
                }
            },
        )

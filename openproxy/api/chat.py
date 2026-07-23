from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.database import get_session
from openproxy.router.proxy import proxy_chat_completion, proxy_streaming_chat_completion

router = APIRouter()


@router.post("/v1/chat/completions")
async def chat_completions(
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

    stream = body.get("stream", False)

    if stream:
        return StreamingResponse(
            proxy_streaming_chat_completion(session, set_name, body),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await proxy_chat_completion(session, set_name, body)
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

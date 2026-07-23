from __future__ import annotations

from collections.abc import AsyncGenerator


async def sse_decode_chunks(
    response_line_iter: AsyncGenerator[bytes, None],
) -> AsyncGenerator[str, None]:
    """Decode Server-Sent Events (SSE) from an httpx streaming response.

    Yields complete SSE data lines (the part after 'data: ').
    """
    buffer = b""
    async for chunk in response_line_iter:
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            decoded = line.decode("utf-8", errors="replace")
            # Only yield lines that carry actual data
            if decoded.startswith("data: "):
                yield decoded[len("data: "):]
            elif decoded == "data: [DONE]":
                yield "[DONE]"

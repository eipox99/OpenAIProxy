from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.models import UsageLog
from openproxy.router.error_classifier import (
    ClassifiedError,
    classify_exception,
    classify_response,
    is_retryable,
)
from openproxy.router.provider_manager import (
    get_model_set_entries,
    record_failure,
    record_success,
)
from openproxy.utils.encryption import decrypt_api_key


async def _log_usage(
    session: AsyncSession,
    request_id: str,
    provider_id: int | None,
    model: str,
    status: str,
    error_type: str | None,
    stream_mode: bool,
    latency_ms: int | None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    log = UsageLog(
        request_id=request_id,
        provider_id=provider_id,
        model=model,
        status=status,
        error_type=error_type,
        stream_mode=stream_mode,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
    )
    session.add(log)
    await session.flush()


def _rewrite_body(body: dict[str, Any], model_name: str, overrides: dict | None = None) -> dict[str, Any]:
    """Return a copy of body with the model field rewritten and overrides applied."""
    new_body = copy.deepcopy(body)
    new_body["model"] = model_name
    if overrides:
        new_body.update(overrides)
    return new_body


async def proxy_chat_completion(
    session: AsyncSession,
    set_name: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Proxy a non-streaming chat completion request through a model set.

    Tries each enabled entry in priority order. If all fail with retryable
    errors, retries the entire set from the beginning (up to set_retry_limit).
    """
    request_id = str(uuid.uuid4())
    entries = await get_model_set_entries(session, set_name)

    if not entries:
        raise RuntimeError(
            f"No entries found for model set '{set_name}'. "
            "Check that the set has enabled entries with active providers."
        )

    from openproxy.utils.settings_helper import get_int_setting
    retry_limit = await get_int_setting(session, "set_retry_limit", 2)

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retry_limit + 1):
            all_retryable = True
            for provider, model_name, overrides in entries:
                api_key = decrypt_api_key(provider.api_key)
                url = f"{provider.base_url.rstrip('/')}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                forwarded_body = _rewrite_body(body, model_name, overrides)

                start = time.monotonic()
                try:
                    resp = await client.post(
                        url, json=forwarded_body, headers=headers, timeout=provider.timeout
                    )
                    latency = int((time.monotonic() - start) * 1000)

                    classified = classify_response(resp)
                    if classified is None:
                        # Success
                        data = resp.json()
                        await record_success(session, provider)
                        usage = data.get("usage", {})
                        await _log_usage(
                            session=session,
                            request_id=request_id,
                            provider_id=provider.id,
                            model=model_name,
                            status="success",
                            error_type=None,
                            stream_mode=False,
                            latency_ms=latency,
                            prompt_tokens=usage.get("prompt_tokens"),
                            completion_tokens=usage.get("completion_tokens"),
                        )
                        await session.commit()
                        return data

                    # Failure
                    await record_failure(session, provider)
                    await _log_usage(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        status="failover",
                        error_type=classified.error_type.value,
                        stream_mode=False,
                        latency_ms=latency,
                    )
                    await session.commit()
                    last_error = f"[{provider.name}:{model_name}] {classified}"

                    if not is_retryable(classified.error_type):
                        raise RuntimeError(
                            f"Non-retryable error from {provider.name}: {classified}"
                        )

                except httpx.HTTPError as exc:
                    latency = int((time.monotonic() - start) * 1000)
                    classified = classify_exception(exc)
                    await record_failure(session, provider)
                    await _log_usage(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        status="failover",
                        error_type=classified.error_type.value,
                        stream_mode=False,
                        latency_ms=latency,
                    )
                    await session.commit()
                    last_error = f"[{provider.name}:{model_name}] {classified}"

            # All entries failed this round with retryable errors
            if attempt < retry_limit:
                await asyncio.sleep(1)
                continue
            break

    raise RuntimeError(
        f"All model set entries failed for '{set_name}' after {retry_limit + 1} attempts. Last error: {last_error}"
    )


async def proxy_streaming_chat_completion(
    session: AsyncSession,
    set_name: str,
    body: dict[str, Any],
) -> AsyncGenerator[bytes, None]:
    """Proxy a streaming chat completion request through a model set.

    Yields raw SSE bytes suitable for a StreamingResponse.
    """
    request_id = str(uuid.uuid4())
    entries = await get_model_set_entries(session, set_name)

    if not entries:
        yield json.dumps({"error": f"No entries found for model set '{set_name}'"}).encode()
        return

    from openproxy.utils.settings_helper import get_int_setting
    retry_limit = await get_int_setting(session, "set_retry_limit", 2)

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retry_limit + 1):
            for provider, model_name, overrides in entries:
                api_key = decrypt_api_key(provider.api_key)
                url = f"{provider.base_url.rstrip('/')}/v1/chat/completions"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                }
                forwarded_body = _rewrite_body(body, model_name, overrides)

                start = time.monotonic()
                try:
                    async with client.stream(
                        "POST", url, json=forwarded_body, headers=headers, timeout=provider.timeout
                    ) as resp:
                        latency = int((time.monotonic() - start) * 1000)

                        if resp.is_error:
                            error_text = await resp.aread()
                            classified = classify_response(resp)
                            await record_failure(session, provider)
                            await _log_usage(
                                session=session,
                                request_id=request_id,
                                provider_id=provider.id,
                                model=model_name,
                                status="failover",
                                error_type=classified.error_type.value if classified else "unknown",
                                stream_mode=True,
                                latency_ms=latency,
                            )
                            await session.commit()
                            last_error = f"[{provider.name}:{model_name}] HTTP {resp.status_code}: {error_text[:200]}"

                            if classified and not is_retryable(classified.error_type):
                                yield json.dumps(
                                    {"error": f"Non-retryable error from {provider.name}: {classified}"}
                                ).encode()
                                return
                            continue  # try next entry

                        # Streaming success
                        await record_success(session, provider)
                        token_count = 0
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                            if chunk.startswith(b"data: ") and chunk != b"data: [DONE]\n\n":
                                token_count += 1

                        await _log_usage(
                            session=session,
                            request_id=request_id,
                            provider_id=provider.id,
                            model=model_name,
                            status="success",
                            error_type=None,
                            stream_mode=True,
                            latency_ms=latency,
                            completion_tokens=token_count,
                        )
                        await session.commit()
                        return  # done

                except httpx.HTTPError as exc:
                    latency = int((time.monotonic() - start) * 1000)
                    classified = classify_exception(exc)
                    await record_failure(session, provider)
                    await _log_usage(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        status="failover",
                        error_type=classified.error_type.value,
                        stream_mode=True,
                        latency_ms=latency,
                    )
                    await session.commit()
                    last_error = f"[{provider.name}:{model_name}] {classified}"

            if attempt < retry_limit:
                await asyncio.sleep(1)
                continue
            break

    yield json.dumps(
        {"error": f"All model set entries failed for '{set_name}'. Last error: {last_error}"}
    ).encode()


async def proxy_embedding(
    session: AsyncSession,
    set_name: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Proxy an embedding request through a model set."""
    request_id = str(uuid.uuid4())
    entries = await get_model_set_entries(session, set_name)

    if not entries:
        raise RuntimeError(f"No entries found for model set '{set_name}'.")

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for provider, model_name, overrides in entries:
            api_key = decrypt_api_key(provider.api_key)
            url = f"{provider.base_url.rstrip('/')}/v1/embeddings"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            forwarded_body = _rewrite_body(body, model_name, overrides)

            start = time.monotonic()
            try:
                resp = await client.post(
                    url, json=forwarded_body, headers=headers, timeout=provider.timeout
                )
                latency = int((time.monotonic() - start) * 1000)

                classified = classify_response(resp)
                if classified is None:
                    data = resp.json()
                    await record_success(session, provider)
                    usage = data.get("usage", {})
                    await _log_usage(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        status="success",
                        error_type=None,
                        stream_mode=False,
                        latency_ms=latency,
                        prompt_tokens=usage.get("prompt_tokens"),
                    )
                    await session.commit()
                    return data

                await record_failure(session, provider)
                await _log_usage(
                    session=session,
                    request_id=request_id,
                    provider_id=provider.id,
                    model=model_name,
                    status="failover",
                    error_type=classified.error_type.value,
                    stream_mode=False,
                    latency_ms=latency,
                )
                await session.commit()
                last_error = f"[{provider.name}:{model_name}] {classified}"

                if not is_retryable(classified.error_type):
                    raise RuntimeError(
                        f"Non-retryable error from {provider.name}: {classified}"
                    )

            except httpx.HTTPError as exc:
                latency = int((time.monotonic() - start) * 1000)
                classified = classify_exception(exc)
                await record_failure(session, provider)
                await _log_usage(
                    session=session,
                    request_id=request_id,
                    provider_id=provider.id,
                    model=model_name,
                    status="failover",
                    error_type=classified.error_type.value,
                    stream_mode=False,
                    latency_ms=latency,
                )
                await session.commit()
                last_error = f"[{provider.name}:{model_name}] {classified}"

    raise RuntimeError(
        f"All model set entries failed for '{set_name}'. Last error: {last_error}"
    )

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from sqlalchemy.exc import OperationalError
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

logger = logging.getLogger(__name__)


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


async def _safe_failover_log(
    session: AsyncSession,
    request_id: str,
    provider_id: int | None,
    model: str,
    error_type: str | None,
    latency_ms: int | None,
) -> None:
    """Log a failover to the database, ignoring DB contention errors.

    Under concurrent load SQLite can return ``database is locked``; we
    accept the data loss and move on so the failover chain isn't blocked.
    """
    try:
        log = UsageLog(
            request_id=request_id,
            provider_id=provider_id,
            model=model,
            status="failover",
            error_type=error_type,
            stream_mode=False,
            latency_ms=latency_ms,
        )
        session.add(log)
        await session.flush()
        await session.commit()
    except OperationalError:
        logger.warning("DB contention logging failover for provider %d", provider_id)
    except Exception:
        logger.exception("Unexpected error logging failover for provider %d", provider_id)


async def _safe_stream_failover_log(
    session: AsyncSession,
    request_id: str,
    provider_id: int | None,
    model: str,
    error_type: str | None,
    latency_ms: int | None,
) -> None:
    """Like _safe_failover_log but for streaming requests."""
    try:
        log = UsageLog(
            request_id=request_id,
            provider_id=provider_id,
            model=model,
            status="failover",
            error_type=error_type,
            stream_mode=True,
            latency_ms=latency_ms,
        )
        session.add(log)
        await session.flush()
        await session.commit()
    except OperationalError:
        logger.warning("DB contention logging stream failover for provider %d", provider_id)
    except Exception:
        logger.exception("Unexpected error logging stream failover for provider %d", provider_id)


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
    logger.info(
        "Chat request start",
        extra={
            "request_id": request_id,
            "set_name": set_name,
            "entry_count": len(entries),
            "stream": False,
        },
    )

    if not entries:
        logger.warning(
            "No entries for model set",
            extra={"request_id": request_id, "set_name": set_name},
        )
        raise RuntimeError(
            f"No entries found for model set '{set_name}'. "
            "Check that the set has enabled entries with active providers."
        )

    from openproxy.utils.settings_helper import get_int_setting
    retry_limit = await get_int_setting(session, "set_retry_limit", 2)

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retry_limit + 1):
            if attempt > 0:
                logger.info(
                    "Retry attempt",
                    extra={
                        "request_id": request_id,
                        "set_name": set_name,
                        "attempt": attempt,
                        "max_attempts": retry_limit + 1,
                    },
                )
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
                        # Success — but some providers return 200 with an error body
                        data = resp.json()
                        if "error" in data:
                            err_msg = str(data["error"])[:500]
                            classified = ClassifiedError(ErrorType.SERVER_ERROR, err_msg, status_code=resp.status_code)
                            await record_failure(session, provider)
                            await _safe_failover_log(
                                session=session,
                                request_id=request_id,
                                provider_id=provider.id,
                                model=model_name,
                                error_type=classified.error_type.value,
                                latency_ms=latency,
                            )
                            last_error = f"[{provider.name}:{model_name}] {classified}"
                            retryable = is_retryable(classified.error_type)
                            logger.warning(
                                "Chat failover (200-with-error)",
                                extra={
                                    "request_id": request_id,
                                    "provider": provider.name,
                                    "model": model_name,
                                    "error_type": classified.error_type.value,
                                    "error_detail": err_msg,
                                    "retryable": retryable,
                                    "latency_ms": latency,
                                },
                            )
                            if not retryable:
                                raise RuntimeError(
                                    f"Non-retryable error from {provider.name}: {classified}"
                                )
                            continue

                        # Genuine success
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
                        logger.info(
                            "Chat success",
                            extra={
                                "request_id": request_id,
                                "provider": provider.name,
                                "model": model_name,
                                "latency_ms": latency,
                            },
                        )
                        return data

                    # Failure — log with DB contention tolerance
                    await record_failure(session, provider)
                    await _safe_failover_log(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        error_type=classified.error_type.value,
                        latency_ms=latency,
                    )
                    last_error = f"[{provider.name}:{model_name}] {classified}"

                    retryable = is_retryable(classified.error_type)
                    cb_active = (
                        provider.cooldown_until is not None
                        if provider.consecutive_failures >= 0
                        else False
                    )
                    logger.warning(
                        "Chat failover",
                        extra={
                            "request_id": request_id,
                            "provider": provider.name,
                            "model": model_name,
                            "error_type": classified.error_type.value,
                            "error_detail": str(classified),
                            "retryable": retryable,
                            "consecutive_failures": provider.consecutive_failures,
                            "circuit_breaker_active": cb_active,
                            "latency_ms": latency,
                        },
                    )

                    if not retryable:
                        raise RuntimeError(
                            f"Non-retryable error from {provider.name}: {classified}"
                        )

                except httpx.HTTPError as exc:
                    latency = int((time.monotonic() - start) * 1000)
                    classified = classify_exception(exc)
                    await record_failure(session, provider)
                    await _safe_failover_log(
                        session=session,
                        request_id=request_id,
                        provider_id=provider.id,
                        model=model_name,
                        error_type=classified.error_type.value,
                        latency_ms=latency,
                    )
                    last_error = f"[{provider.name}:{model_name}] {classified}"
                    cb_active = (
                        provider.cooldown_until is not None
                        if provider.consecutive_failures >= 0
                        else False
                    )
                    logger.warning(
                        "Chat connection error",
                        extra={
                            "request_id": request_id,
                            "provider": provider.name,
                            "model": model_name,
                            "error_type": classified.error_type.value,
                            "error_detail": str(exc),
                            "consecutive_failures": provider.consecutive_failures,
                            "circuit_breaker_active": cb_active,
                            "latency_ms": latency,
                        },
                    )

            # All entries failed this round with retryable errors
            if attempt < retry_limit:
                await asyncio.sleep(1)
                continue
            break

    logger.error(
        "All retries exhausted",
        extra={
            "request_id": request_id,
            "set_name": set_name,
            "attempts": retry_limit + 1,
            "last_error": last_error,
        },
    )
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
    logger.info(
        "Streaming request start",
        extra={
            "request_id": request_id,
            "set_name": set_name,
            "entry_count": len(entries),
            "stream": True,
        },
    )

    if not entries:
        logger.warning(
            "No entries for model set (streaming)",
            extra={"request_id": request_id, "set_name": set_name},
        )
        yield json.dumps({"error": f"No entries found for model set '{set_name}'"}).encode()
        return

    from openproxy.utils.settings_helper import get_int_setting
    retry_limit = await get_int_setting(session, "set_retry_limit", 2)

    last_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(retry_limit + 1):
            if attempt > 0:
                logger.info(
                    "Streaming retry attempt",
                    extra={
                        "request_id": request_id,
                        "set_name": set_name,
                        "attempt": attempt,
                        "max_attempts": retry_limit + 1,
                    },
                )
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
                            error_type = classified.error_type.value if classified else "unknown"
                            last_error = f"[{provider.name}:{model_name}] HTTP {resp.status_code}: {error_text[:200]}"
                            retryable = is_retryable(classified.error_type) if classified else True
                            logger.warning(
                                "Streaming failover",
                                extra={
                                    "request_id": request_id,
                                    "provider": provider.name,
                                    "model": model_name,
                                    "error_type": error_type,
                                    "error_detail": error_text[:200].decode(errors="replace"),
                                    "retryable": retryable,
                                    "latency_ms": latency,
                                },
                            )

                            if classified and not retryable:
                                yield json.dumps(
                                    {"error": f"Non-retryable error from {provider.name}: {classified}"}
                                ).encode()
                                return
                            continue  # try next entry

                        # Streaming success — but some providers return 200 with an error body
                        # Fully read the response to check for embedded errors
                        body = await resp.aread()
                        decoded = body.decode(errors="replace")
                        if '"error"' in decoded and not decoded.startswith("data: "):
                            # Non-SSE error body in 200 response
                            classified = ClassifiedError(ErrorType.SERVER_ERROR, decoded[:500], status_code=resp.status_code)
                            await record_failure(session, provider)
                            last_error = f"[{provider.name}:{model_name}] {classified}"
                            retryable = is_retryable(classified.error_type)
                            logger.warning(
                                "Streaming failover (200-with-error)",
                                extra={
                                    "request_id": request_id,
                                    "provider": provider.name,
                                    "model": model_name,
                                    "error_type": classified.error_type.value,
                                    "error_detail": decoded[:200],
                                    "retryable": retryable,
                                    "latency_ms": latency,
                                },
                            )
                            if not retryable:
                                yield json.dumps(
                                    {"error": f"Non-retryable error from {provider.name}: {classified}"}
                                ).encode()
                                return
                            continue

                        # Genuine SSE stream — yield the entire body
                        await record_success(session, provider)
                        token_count = body.count(b'data: ') - body.count(b'data: [DONE]')
                        yield body

                        # Log usage — response already sent, DB errors tolerated
                        try:
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
                        except OperationalError:
                            logger.warning(
                                "DB contention logging stream success for provider %d",
                                provider.id,
                            )
                        except Exception:
                            logger.exception(
                                "Error logging stream success for provider %d",
                                provider.id,
                            )
                        logger.info(
                            "Streaming success",
                            extra={
                                "request_id": request_id,
                                "provider": provider.name,
                                "model": model_name,
                                "latency_ms": latency,
                                "completion_tokens": token_count,
                            },
                        )
                        return  # done

                except httpx.HTTPError as exc:
                    latency = int((time.monotonic() - start) * 1000)
                    classified = classify_exception(exc)
                    await record_failure(session, provider)
                    last_error = f"[{provider.name}:{model_name}] {classified}"
                    logger.warning(
                        "Streaming connection error",
                        extra={
                            "request_id": request_id,
                            "provider": provider.name,
                            "model": model_name,
                            "error_type": classified.error_type.value,
                            "error_detail": str(exc),
                            "latency_ms": latency,
                        },
                    )

            if attempt < retry_limit:
                await asyncio.sleep(1)
                continue
            break

    logger.error(
        "Streaming all retries exhausted",
        extra={
            "request_id": request_id,
            "set_name": set_name,
            "attempts": retry_limit + 1,
            "last_error": last_error,
        },
    )
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
    logger.info(
        "Embedding request start",
        extra={
            "request_id": request_id,
            "set_name": set_name,
            "entry_count": len(entries),
        },
    )

    if not entries:
        logger.warning(
            "No entries for model set (embedding)",
            extra={"request_id": request_id, "set_name": set_name},
        )
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
                    logger.info(
                        "Embedding success",
                        extra={
                            "request_id": request_id,
                            "provider": provider.name,
                            "model": model_name,
                            "latency_ms": latency,
                        },
                    )
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
                logger.warning(
                    "Embedding failover",
                    extra={
                        "request_id": request_id,
                        "provider": provider.name,
                        "model": model_name,
                        "error_type": classified.error_type.value,
                        "error_detail": str(classified),
                        "latency_ms": latency,
                    },
                )

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
                logger.warning(
                    "Embedding connection error",
                    extra={
                        "request_id": request_id,
                        "provider": provider.name,
                        "model": model_name,
                        "error_type": classified.error_type.value,
                        "error_detail": str(exc),
                        "latency_ms": latency,
                    },
                )

    raise RuntimeError(
        f"All model set entries failed for '{set_name}'. Last error: {last_error}"
    )

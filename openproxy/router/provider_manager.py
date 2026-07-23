from __future__ import annotations

import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from openproxy.models import ModelSet, ModelSetEntry, Provider
from openproxy.utils.settings_helper import get_int_setting


async def get_model_set_entries(
    session: AsyncSession, set_name: str
) -> list[tuple[Provider, str, dict]]:
    """Return ordered list of (provider, model_name_to_forward, overrides_dict).

    Only matches the exact set name. No fallback - each set is isolated.
    Returns empty list if the set doesn't exist.
    """
    now = datetime.datetime.now()

    stmt = (
        select(ModelSet)
        .options(
            selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider)
        )
        .where(ModelSet.name == set_name)
    )
    result = await session.execute(stmt)
    model_set = result.scalar_one_or_none()

    if model_set is None:
        return []

    import json

    # Filter and sort
    entries: list[tuple[Provider, str, dict]] = []
    for entry in model_set.entries:
        if not entry.is_enabled:
            continue
        provider = entry.provider
        if provider is None or not provider.is_active:
            continue
        if provider.cooldown_until and provider.cooldown_until > now:
            continue
        overrides = {}
        if entry.overrides:
            try:
                overrides = json.loads(entry.overrides)
            except (json.JSONDecodeError, TypeError):
                overrides = {}
        entries.append((provider, entry.model_name, overrides))

    # Entries are already ordered by priority via the relationship
    return entries


async def record_failure(session: AsyncSession, provider: Provider) -> None:
    """Increment consecutive_failures and optionally activate circuit breaker."""
    provider.consecutive_failures += 1
    threshold = await get_int_setting(session, "circuit_breaker_threshold", 3)
    cooldown_seconds = await get_int_setting(session, "circuit_breaker_cooldown", 30)

    if provider.consecutive_failures >= threshold:
        provider.cooldown_until = datetime.datetime.now() + datetime.timedelta(
            seconds=cooldown_seconds
        )
    await session.flush()


async def record_success(session: AsyncSession, provider: Provider) -> None:
    """Reset consecutive_failures and clear cooldown on success."""
    provider.consecutive_failures = 0
    provider.cooldown_until = None
    await session.flush()


async def set_provider_cooldown(
    session: AsyncSession, provider_id: int, seconds: int | None = None
) -> None:
    """Manually put a provider into cooldown (used after a failover chain is exhausted)."""
    if seconds is None:
        seconds = await get_int_setting(session, "circuit_breaker_cooldown", 30)
    await session.execute(
        update(Provider)
        .where(Provider.id == provider_id)
        .values(
            cooldown_until=datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        )
    )
    await session.commit()

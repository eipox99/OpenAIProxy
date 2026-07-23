from __future__ import annotations

import datetime
import logging

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from openproxy.models import ModelSet, ModelSetEntry, Provider
from openproxy.utils.settings_helper import get_int_setting

logger = logging.getLogger(__name__)


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
            logger.info(
                "Skipping entry %d in set '%s': entry is disabled",
                entry.id,
                set_name,
            )
            continue
        provider = entry.provider
        if provider is None or not provider.is_active:
            logger.info(
                "Skipping entry %d in set '%s': provider '%s' is %s",
                entry.id,
                set_name,
                provider.name if provider else "None",
                "inactive" if provider else "deleted",
            )
            continue
        if provider.cooldown_until and provider.cooldown_until > now:
            remaining = (provider.cooldown_until - now).total_seconds()
            logger.info(
                "Skipping entry %d in set '%s': provider '%s' in cooldown for %.0fs",
                entry.id,
                set_name,
                provider.name,
                remaining,
            )
            continue
        overrides = {}
        if entry.overrides:
            try:
                overrides = json.loads(entry.overrides)
            except (json.JSONDecodeError, TypeError):
                overrides = {}
        entries.append((provider, entry.model_name, overrides))
        logger.info(
            "Including entry %d in set '%s': provider '%s' model '%s' priority %d",
            entry.id,
            set_name,
            provider.name,
            entry.model_name,
            entry.priority,
        )

    # Entries are already ordered by priority via the relationship
    return entries


async def record_failure(session: AsyncSession, provider: Provider) -> None:
    """Increment consecutive_failures and optionally activate circuit breaker.

    DB write failures (e.g. SQLite locked under concurrent load) are caught
    and logged so the caller can continue the failover chain.
    """
    try:
        provider.consecutive_failures += 1
        threshold = await get_int_setting(session, "circuit_breaker_threshold", 3)
        cooldown_seconds = await get_int_setting(session, "circuit_breaker_cooldown", 30)

        if provider.consecutive_failures >= threshold:
            provider.cooldown_until = datetime.datetime.now() + datetime.timedelta(
                seconds=cooldown_seconds
            )
        await session.flush()
    except OperationalError:
        logger.warning(
            "DB contention recording failure for provider %d (consecutive=%d)",
            provider.id,
            provider.consecutive_failures,
        )
    except Exception:
        logger.exception(
            "Unexpected error recording failure for provider %d",
            provider.id,
        )


async def record_success(session: AsyncSession, provider: Provider) -> None:
    """Reset consecutive_failures and clear cooldown on success.

    DB write failures are caught and logged so the caller can continue.
    """
    try:
        provider.consecutive_failures = 0
        provider.cooldown_until = None
        await session.flush()
    except OperationalError:
        logger.warning(
            "DB contention recording success for provider %d",
            provider.id,
        )
    except Exception:
        logger.exception(
            "Unexpected error recording success for provider %d",
            provider.id,
        )


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

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.config import settings as env_settings
from openproxy.models import Setting

DEFAULTS: dict[str, str] = {
    "circuit_breaker_threshold": "3",
    "circuit_breaker_cooldown": "30",
    "default_timeout": "60",
    "set_retry_limit": "2",
}


async def get_setting(session: AsyncSession, key: str) -> str:
    """Return a setting from the DB, falling back to env vars, then defaults."""
    result = await session.execute(select(Setting.value).where(Setting.key == key))
    row = result.scalar_one_or_none()
    if row:
        return row
    # Fall back to env
    env_key = key.upper()
    env_val = getattr(env_settings, env_key, None)
    if env_val is not None:
        return str(env_val)
    # Fall back to hardcoded default
    return DEFAULTS.get(key, "")


async def get_all_settings(session: AsyncSession) -> dict[str, str]:
    """Return all settings with DB values merged over defaults."""
    result = await session.execute(select(Setting))
    rows = result.scalars().all()
    db_vals = {r.key: r.value for r in rows}
    # Start with defaults, overlay env, then overlay DB
    out: dict[str, str] = dict(DEFAULTS)
    for key in DEFAULTS:
        env_key = key.upper()
        env_val = getattr(env_settings, env_key, None)
        if env_val is not None:
            out[key] = str(env_val)
    out.update(db_vals)
    return out


async def set_setting(session: AsyncSession, key: str, value: str) -> str:
    """Upsert a setting in the DB."""
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
    else:
        setting = Setting(key=key, value=value)
        session.add(setting)
    await session.commit()
    await session.refresh(setting)
    return setting.value


async def get_int_setting(session: AsyncSession, key: str, default: int = 0) -> int:
    val = await get_setting(session, key)
    try:
        return int(val)
    except (ValueError, TypeError):
        return default

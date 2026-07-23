from __future__ import annotations

import datetime
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from openproxy.database import async_session_factory
from openproxy.models import ModelSet, ModelSetEntry, Provider
from openproxy.utils.encryption import decrypt_api_key

logger = logging.getLogger(__name__)

MODEL_SET_NAME = "AutoFreeModels"

# How often the sync runs (set by the background task interval, used for logging)
SYNC_INTERVAL_SECONDS = 1800


async def sync_auto_free_models() -> None:
    """Scan all active providers and sync the AutoFreeModels model set.

    For each active provider that responds successfully, fetch the model list
    from its ``/v1/models`` endpoint, collect every model whose name ends with
    ``"free"`` (case-insensitive), and update the set entries:

    * Add entries for models that appear but are not yet in the set.
    * Remove entries for models that no longer match the criteria.
    * Preserve existing priority and ``is_enabled`` state for entries that
      persist across sync cycles.

    Providers that fail to respond are skipped entirely — their entries are
    left untouched.
    """
    async with async_session_factory() as session:
        # Find or create the AutoFreeModels set
        result = await session.execute(
            select(ModelSet).where(ModelSet.name == MODEL_SET_NAME)
        )
        model_set = result.scalar_one_or_none()

        if model_set is None:
            model_set = ModelSet(
                name=MODEL_SET_NAME,
                is_default=False,
                is_system=True,
            )
            session.add(model_set)
            await session.commit()
            logger.info("Created AutoFreeModels model set")
        elif not model_set.is_system:
            # A user-created set with this name exists — don't hijack it
            logger.warning(
                "A user-created model set named '%s' already exists (not system). "
                "Skipping auto-sync.",
                MODEL_SET_NAME,
            )
            return

        # Fetch all active providers
        providers_result = await session.execute(
            select(Provider).where(Provider.is_active.is_(True))
        )
        providers = providers_result.scalars().all()

        # Build expected entries: {(provider_id, model_name), ...}
        expected_entries: set[tuple[int, str]] = set()
        scanned_provider_ids: set[int] = set()

        for provider in providers:
            api_key = decrypt_api_key(provider.api_key)
            url = f"{provider.base_url.rstrip('/')}/v1/models"

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(
                        url,
                        headers={"Authorization": f"Bearer {api_key}"},
                    )
                if not resp.is_success:
                    logger.warning(
                        "AutoFreeModels: provider '%s' returned HTTP %d — skipping",
                        provider.name,
                        resp.status_code,
                    )
                    continue

                data = resp.json()
                remote_models = [m["id"] for m in data.get("data", []) if m.get("id")]

                # Filter for models ending with "free"
                for model_name in remote_models:
                    if model_name.lower().endswith("free"):
                        expected_entries.add((provider.id, model_name))

                scanned_provider_ids.add(provider.id)

            except httpx.HTTPError as exc:
                logger.warning(
                    "AutoFreeModels: failed to fetch models for provider '%s': %s",
                    provider.name,
                    exc,
                )
                continue
            except Exception:
                logger.exception(
                    "AutoFreeModels: unexpected error scanning provider '%s'",
                    provider.name,
                )
                continue

        if not scanned_provider_ids:
            logger.info("AutoFreeModels: no providers could be scanned, nothing to sync")
            return

        # Load existing entries for the set
        existing_result = await session.execute(
            select(ModelSetEntry).where(
                ModelSetEntry.model_set_id == model_set.id
            )
        )
        existing_entries = existing_result.scalars().all()

        # Index existing by (provider_id, model_name)
        existing_by_key: dict[tuple[int, str], ModelSetEntry] = {}
        for entry in existing_entries:
            key = (entry.provider_id, entry.model_name)
            existing_by_key[key] = entry

        # --- Add new entries ---
        max_priority = max((e.priority for e in existing_entries), default=0)

        added = 0
        for provider_id, model_name in sorted(expected_entries):
            if (provider_id, model_name) not in existing_by_key:
                max_priority += 1
                session.add(
                    ModelSetEntry(
                        model_set_id=model_set.id,
                        provider_id=provider_id,
                        model_name=model_name,
                        priority=max_priority,
                        is_enabled=True,
                    )
                )
                added += 1

        # --- Remove stale entries ---
        # Only remove entries for providers we successfully scanned this cycle.
        # Entries for unscanned providers stay in place.
        removed = 0
        for entry in existing_entries:
            key = (entry.provider_id, entry.model_name)
            if entry.provider_id in scanned_provider_ids and key not in expected_entries:
                await session.delete(entry)
                removed += 1

        if added or removed:
            model_set.last_synced = datetime.datetime.now()
            await session.commit()
            logger.info(
                "AutoFreeModels sync: %d added, %d removed, %d total",
                added,
                removed,
                len(expected_entries),
            )
        else:
            logger.info("AutoFreeModels sync: no changes")

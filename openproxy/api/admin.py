from __future__ import annotations

import datetime
import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from openproxy.database import get_session
from openproxy.models import ModelSet, ModelSetEntry, Provider, ProviderModel, UsageLog
from openproxy.schemas import (
    ModelAddManual,
    ModelSetCreate,
    ModelSetEntryCreate,
    ModelSetOut,
    ModelSetReorderItem,
    ProviderCreate,
    ProviderOut,
    ProviderReorderItem,
    ProviderStats,
    ProviderUpdate,
    StatsResponse,
)
from openproxy.utils.encryption import decrypt_api_key, encrypt_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def normalize_base_url(url: str) -> str:
    """Strip trailing /v1 so the proxy can safely append /v1/..."""
    url = url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


# ============================================================
# Provider CRUD
# ============================================================


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(session: AsyncSession = Depends(get_session)):
    stmt = (
        select(Provider)
        .options(selectinload(Provider.models))
        .order_by(Provider.priority.asc())
    )
    result = await session.execute(stmt)
    providers = result.scalars().unique().all()
    return providers


@router.post("/providers", response_model=ProviderOut, status_code=201)
async def create_provider(
    data: ProviderCreate,
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(
        select(Provider).where(Provider.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Provider name already exists")

    provider = Provider(
        name=data.name,
        base_url=normalize_base_url(data.base_url),
        api_key=encrypt_api_key(data.api_key),
        priority=data.priority,
        timeout=data.timeout,
        is_active=data.is_active,
    )
    session.add(provider)
    await session.commit()
    # Re-fetch with relationships loaded
    stmt = (
        select(Provider)
        .options(selectinload(Provider.models))
        .where(Provider.id == provider.id)
    )
    result = await session.execute(stmt)
    provider = result.scalar_one()
    return provider


@router.get("/providers/{provider_id}", response_model=ProviderOut)
async def get_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Provider)
        .options(selectinload(Provider.models))
        .where(Provider.id == provider_id)
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return provider


@router.put("/providers/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: int,
    data: ProviderUpdate,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Provider)
        .options(selectinload(Provider.models))
        .where(Provider.id == provider_id)
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_values: dict[str, Any] = {}
    if data.name is not None:
        update_values["name"] = data.name
    if data.base_url is not None:
        update_values["base_url"] = normalize_base_url(data.base_url)
    if data.api_key is not None:
        update_values["api_key"] = encrypt_api_key(data.api_key)
    if data.priority is not None:
        update_values["priority"] = data.priority
    if data.timeout is not None:
        update_values["timeout"] = data.timeout
    if data.is_active is not None:
        update_values["is_active"] = data.is_active

    if update_values:
        await session.execute(
            update(Provider).where(Provider.id == provider_id).values(**update_values)
        )
        await session.commit()
        # Re-fetch with relationships
        result = await session.execute(stmt)
        provider = result.scalar_one()

    return provider


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    await session.delete(provider)
    await session.commit()


@router.post("/providers/{provider_id}/toggle", response_model=ProviderOut)
async def toggle_provider(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Provider)
        .options(selectinload(Provider.models))
        .where(Provider.id == provider_id)
    )
    result = await session.execute(stmt)
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider.is_active = not provider.is_active
    await session.commit()
    # Re-fetch
    result = await session.execute(stmt)
    provider = result.scalar_one()
    return provider


@router.post("/providers/reorder")
async def reorder_providers(
    items: list[ProviderReorderItem],
    session: AsyncSession = Depends(get_session),
):
    for item in items:
        await session.execute(
            update(Provider)
            .where(Provider.id == item.id)
            .values(priority=item.priority)
        )
    await session.commit()
    return {"status": "ok"}


# ============================================================
# Test connection
# ============================================================


@router.post("/providers/{provider_id}/test")
async def test_provider_connection(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    api_key = decrypt_api_key(provider.api_key)
    url = f"{provider.base_url.rstrip('/')}/v1/models"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.is_success:
            return {"status": "ok", "message": "Connection successful"}
        else:
            return {
                "status": "error",
                "message": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
    except httpx.HTTPError as exc:
        return {"status": "error", "message": str(exc)}


# ============================================================
# Model management
# ============================================================


@router.post("/providers/{provider_id}/detect-models")
async def detect_models(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Auto-detect models from a provider's /v1/models endpoint."""
    provider = await session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    api_key = decrypt_api_key(provider.api_key)
    url = f"{provider.base_url.rstrip('/')}/v1/models"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if not resp.is_success:
            raise HTTPException(
                status_code=502,
                detail=f"Provider returned HTTP {resp.status_code}",
            )
        data = resp.json()
        remote_models = [m["id"] for m in data.get("data", []) if m.get("id")]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Get existing model names for this provider
    existing_result = await session.execute(
        select(ProviderModel.name).where(ProviderModel.provider_id == provider_id)
    )
    existing_names = set(existing_result.scalars().all())

    added = 0
    for name in remote_models:
        if name not in existing_names:
            session.add(
                ProviderModel(
                    provider_id=provider_id,
                    name=name,
                    is_enabled=True,
                    is_auto_detected=True,
                )
            )
            added += 1
        else:
            # Ensure auto_detected flag is set for previously detected models
            await session.execute(
                update(ProviderModel)
                .where(
                    ProviderModel.provider_id == provider_id,
                    ProviderModel.name == name,
                    ProviderModel.is_auto_detected == False,  # noqa: E712
                )
                .values(is_auto_detected=True)
            )

    await session.commit()
    return {
        "status": "ok",
        "models_found": len(remote_models),
        "models_added": added,
    }


@router.get("/providers/{provider_id}/models")
async def list_provider_models(
    provider_id: int,
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    stmt = (
        select(ProviderModel)
        .where(ProviderModel.provider_id == provider_id)
        .order_by(ProviderModel.name.asc())
    )
    result = await session.execute(stmt)
    models = result.scalars().all()

    return [
        {
            "id": m.id,
            "name": m.name,
            "is_enabled": m.is_enabled,
            "is_auto_detected": m.is_auto_detected,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in models
    ]


@router.post("/providers/{provider_id}/models", status_code=201)
async def add_model_manual(
    provider_id: int,
    data: ModelAddManual,
    session: AsyncSession = Depends(get_session),
):
    provider = await session.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Check duplicate
    existing = await session.execute(
        select(ProviderModel).where(
            ProviderModel.provider_id == provider_id,
            ProviderModel.name == data.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Model already exists for this provider")

    model = ProviderModel(
        provider_id=provider_id,
        name=data.name,
        is_enabled=True,
        is_auto_detected=False,
    )
    session.add(model)
    await session.commit()
    await session.refresh(model)
    return {
        "id": model.id,
        "name": model.name,
        "is_enabled": model.is_enabled,
        "is_auto_detected": model.is_auto_detected,
    }


@router.put("/providers/{provider_id}/models/{model_id}/toggle")
async def toggle_model(
    provider_id: int,
    model_id: int,
    session: AsyncSession = Depends(get_session),
):
    model = await session.get(ProviderModel, model_id)
    if not model or model.provider_id != provider_id:
        raise HTTPException(status_code=404, detail="Model not found")
    model.is_enabled = not model.is_enabled
    await session.commit()
    return {
        "id": model.id,
        "name": model.name,
        "is_enabled": model.is_enabled,
    }


@router.delete("/providers/{provider_id}/models/{model_id}", status_code=204)
async def delete_model(
    provider_id: int,
    model_id: int,
    session: AsyncSession = Depends(get_session),
):
    model = await session.get(ProviderModel, model_id)
    if not model or model.provider_id != provider_id:
        raise HTTPException(status_code=404, detail="Model not found")
    await session.delete(model)
    await session.commit()


# ============================================================
# Statistics
# ============================================================


@router.get("/stats")
async def get_stats(session: AsyncSession = Depends(get_session)):
    # Total requests
    total_stmt = select(func.count(UsageLog.id))
    total_result = await session.execute(total_stmt)
    total = total_result.scalar() or 0

    # By provider (exclude logs where provider was deleted / set to null)
    prov_stmt = (
        select(
            UsageLog.provider_id,
            func.count(UsageLog.id).label("total"),
            func.sum(
                case((UsageLog.status == "success", 1), else_=0)
            ).label("success_count"),
            func.sum(
                case((UsageLog.status == "failover", 1), else_=0)
            ).label("failover_count"),
            func.sum(
                case((UsageLog.status == "error", 1), else_=0)
            ).label("error_count"),
            func.avg(UsageLog.latency_ms).label("avg_latency"),
        )
        .where(UsageLog.provider_id.isnot(None))
        .group_by(UsageLog.provider_id)
    )
    prov_result = await session.execute(prov_stmt)
    by_provider = []
    for row in prov_result:
        pid = row.provider_id
        p = await session.get(Provider, pid) if pid else None
        if p is None:
            continue  # skip providers that have been deleted
        by_provider.append(
            ProviderStats(
                provider_id=pid,
                provider_name=p.name,
                total_requests=row.total,
                success_count=row.success_count,
                failover_count=row.failover_count,
                error_count=row.error_count,
                avg_latency_ms=float(row.avg_latency) if row.avg_latency else None,
            )
        )

    # Recent errors (last 50)
    err_stmt = (
        select(UsageLog)
        .where(UsageLog.status.in_(["failover", "error"]))
        .order_by(UsageLog.timestamp.desc())
        .limit(50)
    )
    err_result = await session.execute(err_stmt)
    recent_errors = []
    for log in err_result.scalars().all():
        recent_errors.append(
            {
                "id": log.id,
                "request_id": log.request_id,
                "provider_id": log.provider_id,
                "model": log.model,
                "status": log.status,
                "error_type": log.error_type,
                "timestamp": (log.timestamp.isoformat() + "Z") if log.timestamp else None,
            }
        )

    return StatsResponse(
        total_requests=total,
        by_provider=by_provider,
        recent_errors=recent_errors,
    )


# ============================================================
# Runtime Settings
# ============================================================


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)):
    from openproxy.utils.settings_helper import get_all_settings

    vals = await get_all_settings(session)
    return [{"key": k, "value": v} for k, v in vals.items()]


@router.put("/settings/{key}")
async def update_setting(
    key: str,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    from openproxy.utils.settings_helper import set_setting

    value = body.get("value", "")
    val = await set_setting(session, key, value)
    return {"key": key, "value": val}


# ============================================================
# Model Set CRUD
# ============================================================


@router.get("/logs")
async def get_logs(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    provider_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(UsageLog)
    if status:
        stmt = stmt.where(UsageLog.status == status)
    if provider_id is not None:
        stmt = stmt.where(UsageLog.provider_id == provider_id)
    stmt = stmt.order_by(UsageLog.timestamp.desc()).limit(limit).offset(offset)

    result = await session.execute(stmt)
    logs = result.scalars().all()

    output = []
    for log in logs:
        pname = "deleted"
        if log.provider_id:
            p = await session.get(Provider, log.provider_id)
            if p:
                pname = p.name
        output.append({
            "id": log.id,
            "request_id": log.request_id,
            "provider_id": log.provider_id,
            "provider_name": pname,
            "model": log.model,
            "status": log.status,
            "error_type": log.error_type,
            "stream_mode": log.stream_mode,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "latency_ms": log.latency_ms,
            "timestamp": (log.timestamp.isoformat() + "Z") if log.timestamp else None,
        })
    return output


@router.get("/logs/count")
async def get_logs_count(
    status: str | None = None,
    provider_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = select(func.count(UsageLog.id))
    if status:
        stmt = stmt.where(UsageLog.status == status)
    if provider_id is not None:
        stmt = stmt.where(UsageLog.provider_id == provider_id)
    result = await session.execute(stmt)
    return {"count": result.scalar() or 0}


# ============================================================
# Model Set CRUD
# ============================================================


@router.get("/model-sets")
async def list_model_sets(session: AsyncSession = Depends(get_session)):
    # Ensure the AutoFreeModels system set exists
    existing = await session.execute(
        select(ModelSet).where(ModelSet.name == "AutoFreeModels")
    )
    if not existing.scalar_one_or_none():
        session.add(ModelSet(name="AutoFreeModels", is_system=True))
        await session.commit()

    stmt = (
        select(ModelSet)
        .options(selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider))
        .order_by(ModelSet.name.asc())
    )
    result = await session.execute(stmt)
    sets = result.scalars().unique().all()
    output = []
    for s in sets:
        entries = []
        for e in s.entries:
            entries.append({
                "id": e.id,
                "model_set_id": e.model_set_id,
                "provider_id": e.provider_id,
                "provider_name": e.provider.name if e.provider else "deleted",
                "model_name": e.model_name,
                "priority": e.priority,
                "overrides": json.loads(e.overrides) if e.overrides else {},
                "is_enabled": e.is_enabled,
            })
        output.append({
            "id": s.id,
            "name": s.name,
            "is_default": s.is_default,
            "is_system": s.is_system,
            "entries": entries,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_synced": s.last_synced.isoformat() if s.last_synced else None,
        })
    return output


@router.post("/model-sets", status_code=201)
async def create_model_set(
    data: ModelSetCreate,
    session: AsyncSession = Depends(get_session),
):
    # Block creation of a set that would shadow the auto-managed system set
    if data.name == "AutoFreeModels":
        raise HTTPException(
            status_code=409,
            detail="'AutoFreeModels' is a reserved name managed by the system",
        )

    existing = await session.execute(
        select(ModelSet).where(ModelSet.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Model set name already exists")

    if data.is_default:
        # Unset any existing default
        await session.execute(
            update(ModelSet).values(is_default=False)
        )

    model_set = ModelSet(name=data.name, is_default=data.is_default)
    session.add(model_set)
    await session.commit()
    # Re-fetch with relationships
    stmt = (
        select(ModelSet)
        .options(selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider))
        .where(ModelSet.id == model_set.id)
    )
    result = await session.execute(stmt)
    model_set = result.scalar_one()
    entries_list = []
    for e in model_set.entries:
        entries_list.append({
            "id": e.id,
            "model_set_id": e.model_set_id,
            "provider_id": e.provider_id,
            "provider_name": e.provider.name if e.provider else "deleted",
            "model_name": e.model_name,
            "priority": e.priority,
            "is_enabled": e.is_enabled,
        })
    return {
        "id": model_set.id,
        "name": model_set.name,
        "is_default": model_set.is_default,
        "is_system": model_set.is_system,
        "entries": entries_list,
        "created_at": model_set.created_at.isoformat() if model_set.created_at else None,
        "last_synced": model_set.last_synced.isoformat() if model_set.last_synced else None,
    }


@router.put("/model-sets/{set_id}")
async def update_model_set(
    set_id: int,
    data: ModelSetCreate,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(ModelSet)
        .options(selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider))
        .where(ModelSet.id == set_id)
    )
    result = await session.execute(stmt)
    model_set = result.scalar_one_or_none()
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")

    # System sets cannot be renamed
    if model_set.is_system and data.name != model_set.name:
        raise HTTPException(
            status_code=403,
            detail="Cannot rename a system-managed model set",
        )

    if data.is_default:
        await session.execute(
            update(ModelSet).values(is_default=False)
        )

    model_set.name = data.name
    model_set.is_default = data.is_default
    await session.commit()
    await session.refresh(model_set)
    # Re-fetch with relationships
    result = await session.execute(stmt)
    model_set = result.scalar_one()
    entries_list = []
    for e in model_set.entries:
        entries_list.append({
            "id": e.id,
            "model_set_id": e.model_set_id,
            "provider_id": e.provider_id,
            "provider_name": e.provider.name if e.provider else "deleted",
            "model_name": e.model_name,
            "priority": e.priority,
            "is_enabled": e.is_enabled,
        })
    return {
        "id": model_set.id,
        "name": model_set.name,
        "is_default": model_set.is_default,
        "is_system": model_set.is_system,
        "entries": entries_list,
        "created_at": model_set.created_at.isoformat() if model_set.created_at else None,
        "last_synced": model_set.last_synced.isoformat() if model_set.last_synced else None,
    }


@router.delete("/model-sets/{set_id}", status_code=204)
async def delete_model_set(
    set_id: int,
    session: AsyncSession = Depends(get_session),
):
    model_set = await session.get(ModelSet, set_id)
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")
    if model_set.is_system:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete a system-managed model set",
        )
    await session.delete(model_set)
    await session.commit()


@router.post("/model-sets/{set_id}/default")
async def set_default_model_set(
    set_id: int,
    session: AsyncSession = Depends(get_session),
):
    model_set = await session.get(ModelSet, set_id)
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")
    await session.execute(update(ModelSet).values(is_default=False))
    model_set.is_default = True
    await session.commit()
    return {"status": "ok", "name": model_set.name, "is_default": True}


# ---- Model Set Entries ----


@router.post("/model-sets/{set_id}/entries", status_code=201)
async def add_model_set_entry(
    set_id: int,
    data: ModelSetEntryCreate,
    session: AsyncSession = Depends(get_session),
):
    model_set = await session.get(ModelSet, set_id)
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")
    if model_set.is_system:
        raise HTTPException(
            status_code=403,
            detail="Cannot add entries to a system-managed model set",
        )

    provider = await session.get(Provider, data.provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    # Get next priority
    result = await session.execute(
        select(func.max(ModelSetEntry.priority)).where(
            ModelSetEntry.model_set_id == set_id
        )
    )
    max_priority = result.scalar() or 0

    entry = ModelSetEntry(
        model_set_id=set_id,
        provider_id=data.provider_id,
        model_name=data.model_name,
        priority=max_priority + 1,
        is_enabled=True,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return {
        "id": entry.id,
        "model_set_id": entry.model_set_id,
        "provider_id": entry.provider_id,
        "provider_name": provider.name,
        "model_name": entry.model_name,
        "priority": entry.priority,
        "is_enabled": entry.is_enabled,
    }


@router.delete("/model-sets/{set_id}/entries/{entry_id}", status_code=204)
async def delete_model_set_entry(
    set_id: int,
    entry_id: int,
    session: AsyncSession = Depends(get_session),
):
    model_set = await session.get(ModelSet, set_id)
    if model_set and model_set.is_system:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete entries from a system-managed model set",
        )
    entry = await session.get(ModelSetEntry, entry_id)
    if not entry or entry.model_set_id != set_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    await session.delete(entry)
    await session.commit()


@router.put("/model-sets/{set_id}/entries/{entry_id}/toggle")
async def toggle_model_set_entry(
    set_id: int,
    entry_id: int,
    session: AsyncSession = Depends(get_session),
):
    entry = await session.get(ModelSetEntry, entry_id)
    if not entry or entry.model_set_id != set_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.is_enabled = not entry.is_enabled
    await session.commit()
    return {
        "id": entry.id,
        "is_enabled": entry.is_enabled,
    }


@router.put("/model-sets/{set_id}/entries/{entry_id}/overrides")
async def update_entry_overrides(
    set_id: int,
    entry_id: int,
    body: dict,
    session: AsyncSession = Depends(get_session),
):
    entry = await session.get(ModelSetEntry, entry_id)
    if not entry or entry.model_set_id != set_id:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.overrides = json.dumps(body.get("overrides", {}))
    await session.commit()
    return {
        "id": entry.id,
        "overrides": json.loads(entry.overrides),
    }


@router.post("/model-sets/{set_id}/entries/reorder")
async def reorder_model_set_entries(
    set_id: int,
    items: list[ModelSetReorderItem],
    session: AsyncSession = Depends(get_session),
):
    for item in items:
        await session.execute(
            update(ModelSetEntry)
            .where(
                ModelSetEntry.id == item.id,
                ModelSetEntry.model_set_id == set_id,
            )
            .values(priority=item.priority)
        )
    await session.commit()
    return {"status": "ok"}


@router.post("/model-sets/{set_id}/sync")
async def sync_model_set(
    set_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Force a sync of a system-managed model set (e.g. AutoFreeModels)."""
    model_set = await session.get(ModelSet, set_id)
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")
    if not model_set.is_system:
        raise HTTPException(
            status_code=400,
            detail="Only system-managed model sets can be synced",
        )

    from openproxy.auto_free_models import sync_auto_free_models

    try:
        await sync_auto_free_models()
    except Exception as exc:
        logger.exception("Force sync failed")
        raise HTTPException(status_code=500, detail=f"Sync failed: {exc}")

    # Expire the stale model_set from the identity map so the next
    # select fetches fresh data from the database.
    await session.refresh(model_set)

    # Re-fetch the set with relationships and return it
    stmt = (
        select(ModelSet)
        .options(selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider))
        .where(ModelSet.id == set_id)
    )
    result = await session.execute(stmt)
    model_set = result.scalar_one()

    entries_list = []
    for e in model_set.entries:
        entries_list.append({
            "id": e.id,
            "model_set_id": e.model_set_id,
            "provider_id": e.provider_id,
            "provider_name": e.provider.name if e.provider else "deleted",
            "model_name": e.model_name,
            "priority": e.priority,
            "is_enabled": e.is_enabled,
        })

    return {
        "id": model_set.id,
        "name": model_set.name,
        "is_default": model_set.is_default,
        "is_system": model_set.is_system,
        "entries": entries_list,
        "created_at": model_set.created_at.isoformat() if model_set.created_at else None,
        "last_synced": model_set.last_synced.isoformat() if model_set.last_synced else None,
    }


@router.post("/model-sets/{set_id}/reorder-by-size")
async def reorder_model_set_by_size(
    set_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Reorder entries in a model set by parameter size (larger = higher priority)."""
    model_set = await session.get(ModelSet, set_id)
    if not model_set:
        raise HTTPException(status_code=404, detail="Model set not found")

    from openproxy.auto_free_models import reorder_by_param_size

    try:
        changed = await reorder_by_param_size(session, set_id)
    except Exception as exc:
        logger.exception("Reorder by size failed")
        raise HTTPException(status_code=500, detail=f"Reorder failed: {exc}")

    await session.commit()
    logger.info("Reorder by size: %d entries changed in set '%s'", changed, model_set.name)

    # Re-fetch the set with relationships and return it
    stmt = (
        select(ModelSet)
        .options(selectinload(ModelSet.entries).selectinload(ModelSetEntry.provider))
        .where(ModelSet.id == set_id)
    )
    result = await session.execute(stmt)
    model_set = result.scalar_one()

    entries_list = []
    for e in model_set.entries:
        entries_list.append({
            "id": e.id,
            "model_set_id": e.model_set_id,
            "provider_id": e.provider_id,
            "provider_name": e.provider.name if e.provider else "deleted",
            "model_name": e.model_name,
            "priority": e.priority,
            "is_enabled": e.is_enabled,
        })

    return {
        "id": model_set.id,
        "name": model_set.name,
        "is_default": model_set.is_default,
        "is_system": model_set.is_system,
        "entries": entries_list,
        "created_at": model_set.created_at.isoformat() if model_set.created_at else None,
        "last_synced": model_set.last_synced.isoformat() if model_set.last_synced else None,
    }

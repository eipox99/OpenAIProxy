from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel, Field


# ---- Provider Schemas ----

class ProviderCreate(BaseModel):
    name: str
    base_url: str
    api_key: str = ""
    priority: int = 0
    timeout: int = 60
    is_active: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    priority: int | None = None
    timeout: int | None = None
    is_active: bool | None = None


class ProviderModelOut(BaseModel):
    id: int
    name: str
    is_enabled: bool
    is_auto_detected: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ProviderOut(BaseModel):
    id: int
    name: str
    base_url: str
    priority: int
    timeout: int
    is_active: bool
    cooldown_until: datetime.datetime | None
    consecutive_failures: int
    models: list[ProviderModelOut] = []
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class ProviderReorderItem(BaseModel):
    id: int
    priority: int


# ---- Model Schemas ----

class ModelAddManual(BaseModel):
    name: str


# ---- Model Set Schemas ----

class ModelSetEntryCreate(BaseModel):
    provider_id: int
    model_name: str


class ModelSetEntryOut(BaseModel):
    id: int
    model_set_id: int
    provider_id: int
    provider_name: str = ""
    model_name: str
    priority: int
    is_enabled: bool

    model_config = {"from_attributes": True}


class ModelSetCreate(BaseModel):
    name: str
    is_default: bool = False


class ModelSetOut(BaseModel):
    id: int
    name: str
    is_default: bool
    entries: list[ModelSetEntryOut] = []
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class ModelSetReorderItem(BaseModel):
    id: int
    priority: int


# ---- OpenAI-Compatible Request/Response ----

class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    n: int | None = None
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    logit_bias: dict[str, float] | None = None
    user: str | None = None
    # Allow extra fields to pass through to upstream
    extra_body: dict[str, Any] | None = None


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str] | list[int] | list[list[int]]
    user: str | None = None


# ---- Stats ----

class ProviderStats(BaseModel):
    provider_id: int
    provider_name: str
    total_requests: int
    success_count: int
    failover_count: int
    error_count: int
    avg_latency_ms: float | None = None


class StatsResponse(BaseModel):
    total_requests: int
    by_provider: list[ProviderStats]
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)

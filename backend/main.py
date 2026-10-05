"""FastAPI backend for Karpathy's LLM Council UI.

The council itself is executed directly by the pinned Amiable engine package. This
backend keeps Karpathy's conversation API, exposes a small UI for Amiable's
runtime-effective settings, and only translates SSE shape where the React
client requires it.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

# Import application config first so .env and LLM_COUNCIL_CONFIG are resolved
# before the Amiable package initializes its global configuration.
from . import config as app_config
from . import storage

from llm_council.council import run_full_council
from llm_council.council_stages import generate_conversation_title
from llm_council.default_pools import default_pool_models
from llm_council.tier_contract import TIER_AGGREGATORS
from llm_council.unified_config import get_config, reload_config
from llm_council.verdict import VerdictType

APP_VERSION = "1.0.0"
PROFILE_NAMES = ("quick", "balanced", "high", "reasoning")

app = FastAPI(title="LLM Council API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""

    pass


class SendMessageRequest(BaseModel):
    """Request to send a message through the Amiable council engine."""

    content: str
    verdict_type: Literal["synthesis", "binary", "tie_breaker"] = "synthesis"
    include_dissent: bool = False


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""

    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""

    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


class SettingsUpdateRequest(BaseModel):
    """Small, stable subset of Amiable settings exposed in the local UI."""

    profile: Literal["quick", "balanced", "high", "reasoning"] = "high"
    use_profile_models: bool = True
    models: List[str] = Field(default_factory=list)
    chairman: Optional[str] = None
    synthesis_mode: Literal["consensus", "debate"] = "consensus"
    exclude_self_votes: bool = True
    style_normalization: Literal["off", "auto", "on"] = "off"
    max_reviewers: Optional[int] = Field(default=None, ge=1, le=10)
    rubric_enabled: bool = False
    safety_enabled: bool = False
    bias_audit_enabled: bool = False
    cache_enabled: bool = False
    cache_ttl_seconds: int = Field(default=0, ge=0, le=2_592_000)
    timeout_multiplier: float = Field(default=1.0, ge=0.1, le=10.0)

    @field_validator("models")
    @classmethod
    def normalize_models(cls, models: List[str]) -> List[str]:
        cleaned: List[str] = []
        for model in models:
            value = model.strip()
            if value and value not in cleaned:
                cleaned.append(value)
        return cleaned

    @field_validator("chairman")
    @classmethod
    def normalize_chairman(cls, chairman: Optional[str]) -> Optional[str]:
        if chairman is None:
            return None
        value = chairman.strip()
        return value or None


class ApiKeyUpdateRequest(BaseModel):
    api_key: str = ""


def _verdict_type(request: SendMessageRequest) -> VerdictType:
    """Map the API's validated string directly to Amiable's verdict enum."""

    return VerdictType(request.verdict_type)


def _style_for_api(value: Any) -> str:
    if value == "auto":
        return "auto"
    return "on" if value is True else "off"


def _style_for_engine(value: str) -> Any:
    if value == "auto":
        return "auto"
    return value == "on"


def _api_key_configured() -> bool:
    value = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    return bool(value and value != "your_openrouter_api_key_here")


def _engine_version() -> str:
    try:
        return version("llm-council-core")
    except PackageNotFoundError:
        return "unknown"


def _settings_payload() -> Dict[str, Any]:
    """Return effective engine settings plus UI option metadata."""

    cfg = get_config()
    pools = default_pool_models()
    profile = cfg.tiers.default if cfg.tiers.default in PROFILE_NAMES else "high"
    profile_models = pools.get(profile, pools["high"])
    current_models = list(cfg.council.models)
    use_profile_models = current_models == profile_models

    available_models = set(current_models)
    for name in PROFILE_NAMES:
        available_models.update(pools.get(name, []))
    available_models.update(TIER_AGGREGATORS.values())
    if cfg.council.chairman:
        available_models.add(cfg.council.chairman)

    profiles = {
        name: {
            "models": pools.get(name, []),
            "chairman": TIER_AGGREGATORS.get(name),
        }
        for name in PROFILE_NAMES
    }

    environment_overrides = [
        name
        for name in (
            "LLM_COUNCIL_MODELS",
            "LLM_COUNCIL_CHAIRMAN",
            "LLM_COUNCIL_MODE",
            "LLM_COUNCIL_EXCLUDE_SELF_VOTES",
            "LLM_COUNCIL_STYLE_NORMALIZATION",
            "LLM_COUNCIL_MAX_REVIEWERS",
            "LLM_COUNCIL_TIMEOUT_MULTIPLIER",
        )
        if os.getenv(name)
    ]

    return {
        "profile": profile,
        "use_profile_models": use_profile_models,
        "models": current_models,
        "chairman": cfg.council.chairman,
        "resolved_chairman": TIER_AGGREGATORS.get(profile),
        "synthesis_mode": cfg.council.synthesis_mode,
        "exclude_self_votes": cfg.council.exclude_self_votes,
        "style_normalization": _style_for_api(cfg.council.style_normalization),
        "max_reviewers": cfg.council.max_reviewers,
        "rubric_enabled": cfg.evaluation.rubric.enabled,
        "safety_enabled": cfg.evaluation.safety.enabled,
        "bias_audit_enabled": cfg.evaluation.bias.audit_enabled,
        "cache_enabled": cfg.cache.enabled,
        "cache_ttl_seconds": cfg.cache.ttl_seconds,
        "timeout_multiplier": cfg.timeouts.multiplier,
        "api_key_configured": _api_key_configured(),
        "config_path": str(app_config.get_council_config_path()),
        "environment_overrides": environment_overrides,
        "options": {
            "profiles": profiles,
            "available_models": sorted(available_models),
        },
        "about": {
            "app_version": APP_VERSION,
            "engine_version": _engine_version(),
            "ui_base": "karpathy/llm-council",
            "engine": "amiable-dev/llm-council",
        },
    }


def _stage_timeout_seconds() -> float:
    """Use Amiable's configured profile timeout for Stage 2/3.

    ``run_full_council`` is otherwise tier-agnostic, but it accepts this native
    timeout input explicitly. This keeps the UI timeout control effective
    without adding a second timeout implementation in the Karpathy backend.
    """

    cfg = get_config()
    return cfg.timeouts.get_timeout(cfg.tiers.default, "per_model") / 1000.0


@app.get("/")
async def root():
    """Health check endpoint."""

    return {"status": "ok", "service": "LLM Council API", "version": APP_VERSION}


@app.get("/api/settings")
async def get_settings():
    """Get the runtime-effective Amiable settings exposed by the UI."""

    return _settings_payload()


@app.put("/api/settings")
async def update_settings(request: SettingsUpdateRequest):
    """Persist UI settings to Amiable YAML and hot-reload the engine config."""

    if not request.use_profile_models and not request.models:
        raise HTTPException(status_code=422, detail="Select at least one council model")

    document = app_config.read_council_config_document()
    sections, council_section = app_config.writable_config_sections(document)
    pools = default_pool_models()

    models = pools[request.profile] if request.use_profile_models else request.models

    tiers_section = sections.setdefault("tiers", {})
    tiers_section["default"] = request.profile

    council_section["models"] = models
    if request.chairman:
        council_section["chairman"] = request.chairman
    else:
        # Missing/None means Amiable resolves the tier-matched chairman itself.
        council_section.pop("chairman", None)
    council_section["synthesis_mode"] = request.synthesis_mode
    council_section["exclude_self_votes"] = request.exclude_self_votes
    council_section["style_normalization"] = _style_for_engine(request.style_normalization)
    if request.max_reviewers is None:
        council_section.pop("max_reviewers", None)
    else:
        council_section["max_reviewers"] = request.max_reviewers

    evaluation = sections.setdefault("evaluation", {})
    evaluation.setdefault("rubric", {})["enabled"] = request.rubric_enabled
    evaluation.setdefault("safety", {})["enabled"] = request.safety_enabled
    evaluation.setdefault("bias", {})["audit_enabled"] = request.bias_audit_enabled

    cache = sections.setdefault("cache", {})
    cache["enabled"] = request.cache_enabled
    cache["ttl_seconds"] = request.cache_ttl_seconds

    timeouts = sections.setdefault("timeouts", {})
    timeouts["multiplier"] = request.timeout_multiplier

    try:
        app_config.write_council_config_document(document)
        reload_config()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not save council config: {exc}") from exc

    return _settings_payload()


@app.put("/api/settings/openrouter-key")
async def update_openrouter_key(request: ApiKeyUpdateRequest):
    """Store the local OpenRouter key without ever returning it to the browser."""

    value = request.api_key.strip()
    if value and value == "your_openrouter_api_key_here":
        raise HTTPException(status_code=422, detail="Enter a real OpenRouter API key")

    try:
        app_config.set_env_value("OPENROUTER_API_KEY", value)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not update .env: {exc}") from exc

    return {"configured": _api_key_configured()}


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""

    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""

    conversation_id = str(uuid.uuid4())
    return storage.create_conversation(conversation_id)


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """Send a message and run Amiable's complete council pipeline."""

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0
    storage.add_user_message(conversation_id, request.content)

    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Direct Amiable engine call. No Karpathy-side council implementation.
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content,
        verdict_type=_verdict_type(request),
        include_dissent=request.include_dissent,
        per_model_timeout=_stage_timeout_seconds(),
    )

    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result,
        metadata,
    )

    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata,
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """Run Amiable directly and expose Karpathy-compatible SSE events.

    Amiable's full orchestrator stays authoritative so its modern ranking,
    verdict, safety/bias, cache and usage logic is not reimplemented here.
    Karpathy's original client expects stage-complete SSE messages, so the
    completed engine result is emitted through that legacy event envelope.
    """

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        title_task = None
        try:
            storage.add_user_message(conversation_id, request.content)

            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Compatibility only: Karpathy's frontend speaks its historical SSE
            # envelope while the actual council run is Amiable's orchestrator.
            yield f"data: {json.dumps({'type': 'stage1_start'})}\n\n"

            stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
                request.content,
                verdict_type=_verdict_type(request),
                include_dissent=request.include_dissent,
                per_model_timeout=_stage_timeout_seconds(),
            )

            yield f"data: {json.dumps({'type': 'stage1_complete', 'data': stage1_results})}\n\n"
            yield f"data: {json.dumps({'type': 'stage2_start'})}\n\n"
            yield f"data: {json.dumps({'type': 'stage2_complete', 'data': stage2_results, 'metadata': metadata})}\n\n"
            yield f"data: {json.dumps({'type': 'stage3_start'})}\n\n"
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result, 'metadata': metadata})}\n\n"

            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            storage.add_assistant_message(
                conversation_id,
                stage1_results,
                stage2_results,
                stage3_result,
                metadata,
            )

            yield f"data: {json.dumps({'type': 'complete', 'metadata': metadata})}\n\n"

        except Exception as exc:
            if title_task and not title_task.done():
                title_task.cancel()
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8001)

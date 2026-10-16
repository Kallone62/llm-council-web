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
import logging
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, List, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

# Import application config first so .env and LLM_COUNCIL_CONFIG are resolved
# before the Amiable package initializes its global configuration.
from . import config as app_config
from . import storage
from .conversation_context import ContextSummaryError, prepare_council_query
from . import engine_runtime

from llm_council.council import run_full_council
from llm_council.council_stages import generate_conversation_title
from llm_council.default_pools import default_pool_models
from llm_council.tier_contract import TIER_AGGREGATORS
from llm_council.unified_config import get_config, reload_config
from llm_council.verdict import VerdictType

engine_runtime.install_stage_hooks()

logger = logging.getLogger(__name__)

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


@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str):
    """Permanently delete a locally stored conversation by its UUID."""
    if not storage.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return Response(status_code=204)


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """Send a message and run Amiable's complete council pipeline."""

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0
    try:
        council_query, new_memory = await prepare_council_query(conversation, request.content)
    except ContextSummaryError as exc:
        raise HTTPException(
            status_code=503,
            detail="Could not prepare AI conversation memory. Please retry.",
        ) from exc
    if new_memory is not None:
        storage.update_conversation_memory(conversation_id, new_memory)
    storage.add_user_message(conversation_id, request.content)

    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Direct Amiable engine call. No Karpathy-side council implementation.
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        council_query,
        verdict_type=_verdict_type(request),
        include_dissent=request.include_dissent,
        per_model_timeout=_stage_timeout_seconds(),
    )

    metadata = engine_runtime.repair_quality_metrics(stage1_results, stage2_results, stage3_result, metadata)

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
    """Stream actual native engine stage boundaries, then full Karpathy data.

    The pinned Amiable run_full_council() has no per-model callback, so progress
    uses transparent wrappers around its native stage coroutines. We do NOT
    claim individual models finished until the engine returns that stage, and
    retain the original full Council result including raw peer critiques.
    """

    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_first_message = len(conversation["messages"]) == 0

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        stage_events: List[Dict[str, Any]] = []
        start_time = time.monotonic()

        async def progress(data: Dict[str, Any]) -> None:
            event_data = {**data, "elapsed_seconds": round(time.monotonic() - start_time, 2)}
            stage_events.append(event_data)
            await queue.put({"type": "engine_progress", "data": event_data})

        async def run_council() -> None:
            title_task = None
            token = engine_runtime.watch_progress(progress)
            try:
                async def memory_progress(state: str) -> None:
                    await progress({"stage": "memory", "state": state})

                council_query, new_memory = await prepare_council_query(
                    conversation, request.content, progress_callback=memory_progress
                )
                if new_memory is not None:
                    storage.update_conversation_memory(conversation_id, new_memory)
                storage.add_user_message(conversation_id, request.content)
                if is_first_message:
                    title_task = asyncio.create_task(generate_conversation_title(request.content))

                stage1, stage2, stage3, metadata = await run_full_council(
                    council_query,
                    verdict_type=_verdict_type(request),
                    include_dissent=request.include_dissent,
                    per_model_timeout=_stage_timeout_seconds(),
                )
                metadata = engine_runtime.repair_quality_metrics(stage1, stage2, stage3, metadata)
                # Keep a small factual stage timeline with the saved conversation.
                metadata = {**metadata, "ui_runtime_progress": list(stage_events)}
                storage.add_assistant_message(
                    conversation_id, stage1, stage2, stage3, metadata
                )

                # Full engine outputs are only available once run_full_council
                # returns. Stage boundaries have already arrived in real time.
                await queue.put({"type": "stage1_complete", "data": stage1})
                await queue.put({
                    "type": "stage2_complete", "data": stage2, "metadata": metadata
                })
                await queue.put({
                    "type": "stage3_complete", "data": stage3, "metadata": metadata
                })
                await queue.put({"type": "complete", "metadata": metadata})

                # Title generation is separate and must not turn a successful
                # Council run into an error after the result is saved.
                if title_task:
                    try:
                        title = await title_task
                        storage.update_conversation_title(conversation_id, title)
                        await queue.put({
                            "type": "title_complete", "data": {"title": title}
                        })
                    except Exception:
                        logger.exception("Could not generate conversation title")
            except ContextSummaryError:
                logger.exception("Could not prepare AI conversation memory")
                await queue.put({
                    "type": "error",
                    "message": "Could not prepare AI conversation memory. Please retry.",
                })
            except asyncio.CancelledError:
                if title_task and not title_task.done():
                    title_task.cancel()
                raise
            except Exception:
                logger.exception("Council execution failed")
                if title_task and not title_task.done():
                    title_task.cancel()
                await queue.put({
                    "type": "error",
                    "message": "Council execution failed. Check the backend terminal for details.",
                })
            finally:
                engine_runtime.unwatch_progress(token)
                await queue.put(None)

        runner = asyncio.create_task(run_council())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except asyncio.TimeoutError:
                    # Transport keepalive, not an invented model-progress event.
                    yield ": keepalive\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            # Browser disconnects cancel expensive model requests promptly.
            if not runner.done():
                runner.cancel()
                try:
                    await runner
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    # Local-only app: do not expose the unauthenticated API or saved conversations
    # to other devices on the network.
    uvicorn.run(app, host="127.0.0.1", port=8001)

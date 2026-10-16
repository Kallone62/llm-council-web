"""Expose *actual* Amiable stage boundaries without replacing its council algorithm.

The pinned 0.53.0 run_full_council() API preserves Karpathy's rich Stage 2
critiques but does not accept progress callbacks. Wrapping its imported stage
coroutines is intentionally narrow: the original functions still do all work,
while a ContextVar isolates event listeners across concurrent browser runs.
"""
from __future__ import annotations

import contextvars
import logging
import asyncio
from typing import Any, Awaitable, Callable, Optional

import llm_council.council as council
from llm_council.quality.integration import calculate_quality_metrics

from .free_profile import FREE_MODELS

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
_current_progress: contextvars.ContextVar[Optional[ProgressCallback]] = (
    contextvars.ContextVar("llm_council_web_progress", default=None)
)
_free_chairman_retry: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "llm_council_web_free_chairman_retry", default=False
)
_chairman_override: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_council_web_chairman_override", default=None
)
# Rate-limits can be account-wide; bounded rotation cannot bypass exhausted
# quotas. Two short pauses only; do not rerun Stage 1 or Stage 2.
FREE_CHAIRMAN_RETRY_PAUSE_SECONDS = 1.0


_STAGE_FUNCTIONS = {
    "stage1_collect_responses": "stage1",
    "stage1_5_normalize_styles": "stage1_5",
    "stage2_collect_rankings": "stage2",
    "stage3_synthesize_final": "stage3",
}


async def emit_progress(stage: str, state: str, **details: Any) -> None:
    """Send only observed stage transitions, never inferred model completions."""
    callback = _current_progress.get()
    if callback is not None:
        try:
            await callback({"stage": stage, "state": state, **details})
        except Exception:
            logger.warning("Could not deliver council progress", exc_info=True)


async def _run_free_stage3_with_fallback(original: Callable, *args, **kwargs):
    """Retry only failed Stage 3 synthesis with another *known free* Chairman.

    Calls Amiable's original Stage 3 for every attempt, retaining its verdict
    prompt, parsing, token accounting and native result format. A context-local
    Chairman override prevents simultaneous sessions from sharing mutations.
    We never ask the engine to rerun costly Stage 1 or Stage 2.
    """
    result = await original(*args, **kwargs)
    if result[0].get("error_status") != "rate_limited":
        return result

    primary = council._get_chairman_model()
    alternatives = [model for model in FREE_MODELS if model != primary]
    for model in alternatives:
        await emit_progress("stage3", "retrying", model=model)
        await asyncio.sleep(FREE_CHAIRMAN_RETRY_PAUSE_SECONDS)
        token = _chairman_override.set(model)
        try:
            result = await original(*args, **kwargs)
        finally:
            _chairman_override.reset(token)
        if result[0].get("error_status") != "rate_limited":
            return result

    failed, usage, verdict = result
    failed = {
        **failed,
        "response": (
            "Free Chairman is temporarily unavailable: all configured free "
            "models were rate-limited. Please wait for OpenRouter's limits "
            "to reset and try again. Stage 1 and Stage 2 results are saved."
        ),
        "error_detail": "All free Chairman candidates were rate-limited.",
        "free_fallback_exhausted": True,
    }
    return failed, usage, verdict


def wrap_stage(stage: str, original: Callable) -> Callable:
    """Transparent async decorator, also independently testable offline."""
    async def wrapped(*args, **kwargs):
        await emit_progress(stage, "started")
        try:
            if stage == "stage3" and _free_chairman_retry.get():
                result = await _run_free_stage3_with_fallback(original, *args, **kwargs)
            else:
                result = await original(*args, **kwargs)
        except BaseException:
            await emit_progress(stage, "failed")
            raise
        details = {}
        if stage == "stage1" and isinstance(result, tuple):
            details["responses"] = len(result[0])
        elif stage == "stage2" and isinstance(result, tuple):
            details["reviews"] = len(result[0])
        if stage == "stage3" and result[0].get("error_status"):
            await emit_progress(stage, "failed", error_status=result[0]["error_status"])
        else:
            await emit_progress(stage, "completed", **details)
        return result

    wrapped.__name__ = original.__name__
    wrapped.__doc__ = original.__doc__
    wrapped._council_web_progress_wrapper = True
    return wrapped


def install_stage_hooks() -> None:
    """Install once, preserving Amiable Stage 3 and all native tier settings."""
    # Amiable Stage 3 delegates to council._get_chairman_model at call-time.
    # Override only while a guarded Free execution is trying an alternate.
    original_get_chairman = council._get_chairman_model
    if not getattr(original_get_chairman, "_council_web_free_override", False):
        def context_local_chairman():
            return _chairman_override.get() or original_get_chairman()

        context_local_chairman._council_web_free_override = True
        council._get_chairman_model = context_local_chairman

    for name, stage in _STAGE_FUNCTIONS.items():
        original = getattr(council, name)
        if not getattr(original, "_council_web_progress_wrapper", False):
            setattr(council, name, wrap_stage(stage, original))


def watch_free_chairman_retry(enabled: bool):
    """Opt in only for a fully guarded Free-profile execution."""
    return _free_chairman_retry.set(enabled)


def unwatch_free_chairman_retry(token) -> None:
    _free_chairman_retry.reset(token)


def watch_progress(callback: ProgressCallback):
    """Return a ContextVar token for the current Council execution."""
    return _current_progress.set(callback)


def unwatch_progress(token) -> None:
    _current_progress.reset(token)


def repair_quality_metrics(stage1: list, stage2: list, stage3: dict, metadata: dict) -> dict:
    """Recalculate Amiable's quality metric with the actual synthesis *text*.

    In core 0.53.0, run_full_council passes a {model, response} dictionary to
    the quality calculator, while its extraction helper expects {content}.
    This produces zero alignment and a false 1.0 hallucination-risk score.
    We invoke Amiable's own metric function with its supported string input,
    never implement or reinterpret the metric ourselves.
    """
    if not isinstance(metadata, dict) or "quality_metrics" not in metadata:
        return metadata
    if stage3.get("error_status"):
        # Do not score a provider error as a successfully synthesized answer.
        return {**metadata, "quality_metrics": None}
    synthesis = stage3.get("response") if isinstance(stage3, dict) else None
    if not isinstance(synthesis, str) or not synthesis:
        return metadata
    try:
        stage1_dict = {
            item["model"]: {"content": item["response"]}
            for item in stage1
            if isinstance(item, dict) and item.get("model") and item.get("response")
        }
        ordered = [
            (item["model"], item["average_position"])
            for item in metadata.get("aggregate_rankings", [])
            if isinstance(item, dict)
            and item.get("model")
            and isinstance(item.get("average_position"), (float, int))
        ]
        if not stage1_dict or len(ordered) < 2:
            return metadata
        corrected = calculate_quality_metrics(
            stage1_responses=stage1_dict,
            stage2_rankings=stage2,
            stage3_synthesis=synthesis,
            aggregate_rankings=ordered,
            label_to_model=metadata.get("label_to_model"),
        ).to_dict()
        return {**metadata, "quality_metrics": corrected}
    except Exception:
        logger.warning("Quality metric correction failed; preserving native result", exc_info=True)
        return metadata

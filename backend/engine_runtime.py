"""Expose *actual* Amiable stage boundaries without replacing its council algorithm.

The pinned 0.53.0 run_full_council() API preserves Karpathy's rich Stage 2
critiques but does not accept progress callbacks. Wrapping its imported stage
coroutines is intentionally narrow: the original functions still do all work,
while a ContextVar isolates event listeners across concurrent browser runs.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Awaitable, Callable, Optional

import llm_council.council as council
from llm_council.quality.integration import calculate_quality_metrics

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
_current_progress: contextvars.ContextVar[Optional[ProgressCallback]] = (
    contextvars.ContextVar("llm_council_web_progress", default=None)
)

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


def wrap_stage(stage: str, original: Callable) -> Callable:
    """Transparent async decorator, also independently testable offline."""
    async def wrapped(*args, **kwargs):
        await emit_progress(stage, "started")
        try:
            result = await original(*args, **kwargs)
        except BaseException:
            await emit_progress(stage, "failed")
            raise
        details = {}
        if stage == "stage1" and isinstance(result, tuple):
            details["responses"] = len(result[0])
        elif stage == "stage2" and isinstance(result, tuple):
            details["reviews"] = len(result[0])
        await emit_progress(stage, "completed", **details)
        return result

    wrapped.__name__ = original.__name__
    wrapped.__doc__ = original.__doc__
    wrapped._council_web_progress_wrapper = True
    return wrapped


def install_stage_hooks() -> None:
    """Install once; no effect on executions without a progress subscriber."""
    for name, stage in _STAGE_FUNCTIONS.items():
        original = getattr(council, name)
        if not getattr(original, "_council_web_progress_wrapper", False):
            setattr(council, name, wrap_stage(stage, original))


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

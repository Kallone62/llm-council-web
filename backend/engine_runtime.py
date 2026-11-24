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
from llm_council.council_stages import early_consensus_enabled
from llm_council.council_usage import _add_cost_to_usage
from llm_council.gateway_adapter import STATUS_OK, STATUS_RATE_LIMITED, query_model_with_status
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
# Only observed Stage 1 rate limits, isolated to the guarded Free round.
_stage1_rate_limits: contextvars.ContextVar[dict[str, int | None] | None] = (
    contextvars.ContextVar("llm_council_web_stage1_rate_limits", default=None)
)
_missing_reviewers: contextvars.ContextVar[tuple[str, ...]] = (
    contextvars.ContextVar("llm_council_web_missing_reviewers", default=())
)


_all_reviews_missing: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "llm_council_web_all_reviews_missing", default=False
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


def _retryable_free_chairman_failure(result: tuple) -> bool:
    """Retry only known free-provider/routing failures, not auth or code errors.

    Amiable 0.53.0 maps gateway HTTP 404/5xx to a generic 'error' status while
    preserving the HTTP status code in error_detail. A failed :free endpoint
    can therefore be retryable even though it is not a 429 rate limit.
    """
    response = result[0]
    status = response.get("error_status")
    if status == "rate_limited":
        return True
    if status != "error":
        return False
    detail = str(response.get("error_detail") or "")
    # Only provider HTTP errors. Auth, billing, invalid payloads and arbitrary
    # exceptions must not trigger another API request.
    import re
    return bool(re.search(r"\b(?:404|50[0-9]|51[0-9]|52[0-9]|53[0-9])\b", detail))


async def _run_free_stage3_with_fallback(original: Callable, *args, **kwargs):
    """Retry only Amiable's native Stage 3 using fixed zero-priced Chairmen.

    Do not repeat Stage 1/2, change Amiable's tier/model pools, or route to a
    paid API. In particular, continue after a rate-limited primary and a
    provider-404 secondary rather than treating that 404 as a successful final
    synthesis. The Chairman override is local to this asyncio task.
    """
    configured_primary = council._get_chairman_model()
    blocked = set(_stage1_rate_limits.get() or {}) | set(_missing_reviewers.get())
    candidates = [model for model in (configured_primary, *FREE_MODELS)
                  if model not in blocked]
    candidates = list(dict.fromkeys(candidates))
    if not candidates:
        return ({"model": configured_primary,
                 "response": "All Free Chairman candidates are rate-limited. Please wait and try again.",
                 "error_status": "rate_limited"},
                {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, None)
    primary, *alternatives = candidates
    if primary != configured_primary:
        await emit_progress("stage3", "retrying", model=primary)
        await asyncio.sleep(FREE_CHAIRMAN_RETRY_PAUSE_SECONDS)
        token = _chairman_override.set(primary)
        try:
            result = await original(*args, **kwargs)
        finally:
            _chairman_override.reset(token)
    else:
        result = await original(*args, **kwargs)
    attempts = [{
        "model": result[0].get("model", primary),
        "status": result[0].get("error_status", "ok"),
    }]
    if not _retryable_free_chairman_failure(result):
        return result

    # The compatibility review API cannot identify the HTTP cause of a
    # missing review. With no replies from the full council and a confirmed
    # Chairman 429, another model rotation is unlikely to help. Do not claim
    # that the account quota is known to be exhausted.
    if _all_reviews_missing.get() and result[0].get("error_status") == "rate_limited":
        response, usage, verdict = result
        return ({**response,
                 "response": "No reviewers responded and the Free Chairman is rate-limited. "
                             "This may be an account-wide limit. Please wait and try again. "
                             "Individual responses are saved.",
                 "free_fallback_suppressed": "all_reviews_missing"}, usage, verdict)

    for model in alternatives:
        await emit_progress("stage3", "retrying", model=model)
        await asyncio.sleep(FREE_CHAIRMAN_RETRY_PAUSE_SECONDS)
        token = _chairman_override.set(model)
        try:
            result = await original(*args, **kwargs)
        finally:
            _chairman_override.reset(token)
        attempts.append({
            "model": result[0].get("model", model),
            "status": result[0].get("error_status", "ok"),
        })
        # A successful synthesis is a success; non-retryable errors also stop
        # to avoid hiding credential, billing, or prompt problems.
        if not _retryable_free_chairman_failure(result):
            return result

    failed, usage, verdict = result
    failed = {
        **failed,
        "response": (
            "Free Chairman could not generate the final answer. The available "
            "free models were rate-limited or returned provider errors. "
            "Please wait and try again; an account-wide daily limit cannot "
            "be bypassed by changing the model. Stage 1 and Stage 2 results "
            "are saved."
        ),
        "error_detail": "No available Free Chairman succeeded.",
        "free_fallback_exhausted": True,
        "free_chairman_attempts": attempts,
    }
    return failed, usage, verdict


def normalize_stage3_response(stage3: dict) -> dict:
    """Treat missing or blank synthesis as failure, preserving native errors."""
    if stage3.get("error_status"):
        return stage3
    synthesis = stage3.get("response")
    if not isinstance(synthesis, str) or not synthesis.strip():
        return {
            **stage3,
            "response": "Chairman returned no usable final synthesis.",
            "error_status": "empty_synthesis",
        }
    return stage3


def summarize_peer_reviews(rankings: list, label_to_model: dict) -> dict:
    """Describe the native parsed ballots without changing the ranking algorithm."""
    labels = set(label_to_model or {})
    counts = {"valid_reviews": 0, "partial_reviews": 0, "invalid_reviews": 0,
              "abstained_reviews": 0}
    for review in rankings:
        parsed = review.get("parsed_ranking") if isinstance(review, dict) else None
        if isinstance(parsed, dict) and parsed.get("abstained"):
            counts["abstained_reviews"] += 1
            continue
        ballot = parsed.get("ranking") if isinstance(parsed, dict) else None
        known = (
            {label for label in ballot if isinstance(label, str) and label in labels}
            if isinstance(ballot, list) else set()
        )
        if not known:
            counts["invalid_reviews"] += 1
        elif known == labels and len(ballot) == len(labels):
            counts["valid_reviews"] += 1
        else:
            counts["partial_reviews"] += 1
    if not (counts["valid_reviews"] or counts["partial_reviews"]):
        state = "failed"
    elif counts["valid_reviews"] == len(rankings):
        state = "completed"
    else:
        state = "partial"
    return {"state": state, "reviews": len(rankings), **counts}


def annotate_peer_review(stage1: list, stage2: list, stage3: dict, metadata: dict) -> dict:
    """Expose review coverage and native effective votes, including cached runs."""
    metadata = dict(metadata or {})
    review = summarize_peer_reviews(stage2, metadata.get("label_to_model"))
    votes = [
        item.get("vote_count") for item in (metadata.get("aggregate_rankings") or [])
        if isinstance(item, dict)
    ]
    positive = [count for count in votes if type(count) is int and count > 0]
    review.update(effective_votes=sum(positive), ranked_candidates=len(positive))
    if not stage1:
        review["state"] = "not_run"
    elif len(stage1) == 1 and not stage2:
        # Native single-model degradation deliberately skips peer review.
        review["state"] = "skipped"
    elif not positive:
        review["state"] = "failed"
    elif review["state"] == "completed" and len(positive) < len(stage1):
        review["state"] = "partial"
    missing = _missing_reviewers.get()
    if missing:
        review["missing_reviewers"] = list(missing)
        if review["state"] == "completed":
            review["state"] = "partial"
    metadata["peer_review"] = review
    if (
        review["state"] in ("failed", "partial")
        and not stage3.get("error_status") and metadata.get("status") != "failed"
    ):
        metadata["status"] = "partial"
    if "quality_metrics" in metadata and _peer_metrics_unavailable(review):
        metadata["quality_metrics"] = None
    return metadata


def _peer_metrics_unavailable(review: dict) -> bool:
    return review.get("state") in ("failed", "skipped", "not_run") or (
        review.get("state") == "partial" and review.get("ranked_candidates", 0) < 2
    )


async def _collect_free_stage1(user_query: str) -> tuple:
    """Preserve native HTTP status with the compatibility path's cancellation semantics.

    Amiable 0.53.0's status-aware Stage 1 uses as_completed without cancelling
    its direct HTTP tasks when the round is cancelled. Use the same native
    gateway/status client and usage helper, but own the task lifetime here.
    Only the Free adapter's collection/projection is local; deliberation,
    ranking, synthesis and model request construction stay in the engine.
    """
    models = list(council._get_council_models())
    messages = [{"role": "user", "content": user_query}]
    tasks = [asyncio.create_task(query_model_with_status(model, messages, timeout=120.0))
             for model in models]
    try:
        statuses = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    responses = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    limits = {}
    for model, status in zip(models, statuses):
        if status["status"] == STATUS_RATE_LIMITED:
            limits[model] = status.get("retry_after")
        if status["status"] == STATUS_OK:
            responses.append({"model": model, "response": status.get("content") or ""})
            model_usage = status.get("usage", {})
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage[key] += model_usage.get(key, 0)
            _add_cost_to_usage(usage, model_usage, model=model)
    _stage1_rate_limits.set(limits)
    return responses, usage


def wrap_stage(stage: str, original: Callable) -> Callable:
    """Transparent async decorator, also independently testable offline."""
    async def wrapped(*args, **kwargs):
        await emit_progress(stage, "started")
        try:
            if stage == "stage1" and _free_chairman_retry.get():
                result = await _collect_free_stage1(*args, **kwargs)
            elif stage == "stage2" and _free_chairman_retry.get():
                models = kwargs.get("models")
                if models is None:
                    models = council._get_council_models()
                blocked = _stage1_rate_limits.get() or {}
                models = [model for model in models if model not in blocked]
                if blocked:
                    # No more calls to a rate-limited model in this round.
                    kwargs = {**kwargs, "models": models}
                limit = council._get_max_reviewers()
                full_review = (limit is None or limit >= len(models)) and not early_consensus_enabled()
                result = await original(*args, **kwargs)
                # The compatibility review API loses HTTP status. Only infer
                # a missing review when every reviewer was actually selected;
                # do not label it 429 or confuse intentional sampling with failure.
                if full_review and not result[0]:
                    candidates = {item["model"] for item in result[1].values()}
                    _all_reviews_missing.set(bool(models) and set(models) == candidates)
                if full_review and result[0]:
                    candidates = {item["model"] for item in result[1].values()}
                    received = {item["model"] for item in result[0]}
                    _missing_reviewers.set(tuple(model for model in models
                                                 if model in candidates and model not in received))
            elif stage == "stage3" and _free_chairman_retry.get():
                result = await _run_free_stage3_with_fallback(original, *args, **kwargs)
            else:
                result = await original(*args, **kwargs)
            if stage == "stage3":
                response = normalize_stage3_response(result[0])
                if response is not result[0]:
                    result = (response, *result[1:])
        except BaseException:
            await emit_progress(stage, "failed")
            raise
        details = {}
        if stage == "stage1" and isinstance(result, tuple):
            details["responses"] = len(result[0])
            if _stage1_rate_limits.get():
                details["rate_limits"] = dict(_stage1_rate_limits.get())
        elif stage == "stage2" and isinstance(result, tuple):
            details = summarize_peer_reviews(result[0], result[1])
            state = details.pop("state")
            if _missing_reviewers.get():
                details["missing_reviewers"] = list(_missing_reviewers.get())
                if state == "completed":
                    state = "partial"
            await emit_progress(stage, state, **details)
            return result
        if stage == "stage1" and details.get("responses") == 0:
            await emit_progress(stage, "failed", **details)
        elif stage == "stage3" and result[0].get("error_status"):
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
    return (_free_chairman_retry.set(enabled), _stage1_rate_limits.set(None),
            _missing_reviewers.set(()), _all_reviews_missing.set(False))


def unwatch_free_chairman_retry(token) -> None:
    enabled_token, limits_token, reviewers_token, all_missing_token = token
    _all_reviews_missing.reset(all_missing_token)
    _missing_reviewers.reset(reviewers_token)
    _stage1_rate_limits.reset(limits_token)
    _free_chairman_retry.reset(enabled_token)


def stage1_rate_limits() -> dict:
    return dict(_stage1_rate_limits.get() or {})


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
    if (
        _peer_metrics_unavailable(metadata.get("peer_review", {}))
        or normalize_stage3_response(stage3).get("error_status")
    ):
        # Do not score a provider error or missing synthesis as a valid answer.
        return {**metadata, "quality_metrics": None}
    synthesis = stage3["response"]
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

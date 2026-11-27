"""Response caching for determinism and development efficiency.

Provides optional caching of council responses to:
- Speed up development iteration (instant responses for repeated queries)
- Save API costs during testing
- Enable deterministic testing (same query = same response)
- Allow offline inspection of previous responses

Usage:
    export LLM_COUNCIL_CACHE=true
    export LLM_COUNCIL_CACHE_TTL=3600  # seconds, 0 = infinite
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# ADR-032: Migrated to unified_config
from llm_council.unified_config import get_config
from .chairman import current_tier, resolve_chairman


def _get_cache_config():
    """Get cache configuration from unified config."""
    return get_config().cache


def _get_council_config():
    """Get council configuration from unified config."""
    return get_config().council


# Lazy-loaded accessors for cache config
def _cache_enabled() -> bool:
    return _get_cache_config().enabled


def _cache_ttl() -> int:
    return _get_cache_config().ttl_seconds


def _cache_dir():
    return _get_cache_config().directory


# Lazy-loaded accessors for council config (used in cache key generation)
def _council_models() -> list:
    return _get_council_config().models


def _chairman_model() -> str:
    return resolve_chairman()  # #725: the tier in scope, else the default tier's


def _synthesis_mode() -> str:
    return _get_council_config().synthesis_mode


def _exclude_self_votes() -> bool:
    return _get_council_config().exclude_self_votes


def _style_normalization():
    return _get_council_config().style_normalization


def _max_reviewers():
    return _get_council_config().max_reviewers


# Keep legacy attribute access and explicit test overrides, but do not snapshot
# configuration at import time: the web application reloads it while running.
_CONFIG_ACCESSORS = {
    "CACHE_ENABLED": _cache_enabled,
    "CACHE_TTL": _cache_ttl,
    "CACHE_DIR": _cache_dir,
    "COUNCIL_MODELS": _council_models,
    "CHAIRMAN_MODEL": _chairman_model,
    "SYNTHESIS_MODE": _synthesis_mode,
    "EXCLUDE_SELF_VOTES": _exclude_self_votes,
    "STYLE_NORMALIZATION": _style_normalization,
    "MAX_REVIEWERS": _max_reviewers,
}


def _effective(name):
    return globals()[name] if name in globals() else _CONFIG_ACCESSORS[name]()


def __getattr__(name):
    if name in _CONFIG_ACCESSORS:
        return _effective(name)
    raise AttributeError(name)


def get_cache_key(query: str, *, verdict_type="synthesis", include_dissent=False,
                  per_model_timeout=None) -> str:
    """Key the response by the query and current response-affecting settings.

    The schema marker prevents reuse of entries made before verdict/dissent
    and runtime configuration were included in the key. No credentials are
    included. Cache enablement, directory and TTL govern storage, not identity.
    """
    config = get_config()
    gateway = config.gateways
    cache_input = {
        "schema": 2,
        "query": query,
        "council_models": sorted(_effective("COUNCIL_MODELS")),
        "chairman": _chairman_model(),
        "chairman_disabled": config.council.chairman_disabled,
        "tier": current_tier(),
        "synthesis_mode": _effective("SYNTHESIS_MODE"),
        "exclude_self_votes": _effective("EXCLUDE_SELF_VOTES"),
        "style_normalization": _effective("STYLE_NORMALIZATION"),
        "normalizer_model": config.council.normalizer_model,
        "max_reviewers": _effective("MAX_REVIEWERS"),
        "verdict_type": getattr(verdict_type, "value", verdict_type),
        "include_dissent": include_dissent,
        "per_model_timeout": per_model_timeout,
        "evaluation": config.evaluation.model_dump(mode="json"),
        "gateway": gateway.default,
        "model_routing": gateway.model_routing,
        "model_name_map": gateway.model_name_map,
        "fallback": gateway.fallback.model_dump(mode="json"),
    }
    serialized = json.dumps(cache_input, sort_keys=True)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def get_cached_response(cache_key: str) -> Optional[Dict[str, Any]]:
    """Retrieve cached response if it exists and is not expired.

    Args:
        cache_key: The cache key from get_cache_key()

    Returns:
        Cached response dict if valid cache hit, None otherwise
    """
    if not _effective("CACHE_ENABLED"):
        return None

    cache_file = _effective("CACHE_DIR") / f"{cache_key}.json"

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, "r") as f:
            cached = json.load(f)

        # Check TTL if configured
        ttl = _effective("CACHE_TTL")
        if ttl > 0:
            cached_at = cached.get("_cached_at", 0)
            if time.time() - cached_at > ttl:
                # Cache expired, delete file
                cache_file.unlink(missing_ok=True)
                return None

        return cached
    except (json.JSONDecodeError, OSError):
        # Invalid cache file, delete it
        cache_file.unlink(missing_ok=True)
        return None


def save_to_cache(
    cache_key: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
    stage3_result: Dict[str, Any],
    metadata: Dict[str, Any],
) -> None:
    """Save council response to cache.

    Args:
        cache_key: The cache key from get_cache_key()
        stage1_results: Results from Stage 1
        stage2_results: Results from Stage 2
        stage3_result: Result from Stage 3
        metadata: Response metadata
    """
    if not _effective("CACHE_ENABLED"):
        return

    # Create cache directory if needed
    _effective("CACHE_DIR").mkdir(parents=True, exist_ok=True)

    cache_file = _effective("CACHE_DIR") / f"{cache_key}.json"

    cache_data = {
        "_cached_at": time.time(),
        "_cache_key": cache_key,
        "stage1_results": stage1_results,
        "stage2_results": stage2_results,
        "stage3_result": stage3_result,
        "metadata": metadata,
    }

    try:
        with open(cache_file, "w") as f:
            json.dump(cache_data, f, indent=2, default=str)
    except OSError:
        # Silently fail on cache write errors
        pass


def clear_cache() -> int:
    """Clear all cached responses.

    Returns:
        Number of cache entries deleted
    """
    directory = _effective("CACHE_DIR")
    if not directory.exists():
        return 0

    count = 0
    for cache_file in directory.glob("*.json"):
        try:
            cache_file.unlink()
            count += 1
        except OSError:
            pass

    return count


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics.

    Returns:
        Dict with cache stats (entry count, total size, oldest/newest)
    """
    directory = _effective("CACHE_DIR")
    if not directory.exists():
        return {
            "enabled": _effective("CACHE_ENABLED"),
            "entries": 0,
            "total_size_bytes": 0,
            "cache_dir": str(directory),
        }

    entries = list(directory.glob("*.json"))
    total_size = sum(f.stat().st_size for f in entries)

    stats = {
        "enabled": _effective("CACHE_ENABLED"),
        "entries": len(entries),
        "total_size_bytes": total_size,
        "cache_dir": str(directory),
        "ttl_seconds": _effective("CACHE_TTL") or "infinite",
    }

    if entries:
        mtimes = [f.stat().st_mtime for f in entries]
        stats["oldest_entry"] = time.ctime(min(mtimes))
        stats["newest_entry"] = time.ctime(max(mtimes))

    return stats

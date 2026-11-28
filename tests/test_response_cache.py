"""Native response cache: real hot reload, key isolation, TTL and filesystem."""
import json
import time

import pytest
import yaml

from llm_council import cache
from llm_council.unified_config import reload_config


@pytest.fixture
def configure_cache(tmp_path, monkeypatch):
    path = tmp_path / "cache-config.yaml"
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(path))
    config = {"tiers": {"default": "quick"},
              "council": {"models": ["a/one", "b/two", "c/three"], "chairman": "a/one"},
              "cache": {"enabled": False, "ttl_seconds": 0, "directory": str(tmp_path / "cache")}}

    def update(**changes):
        for group, settings in changes.items():
            config.setdefault(group, {}).update(settings)
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return reload_config()

    update()
    return update, tmp_path / "cache"


def save(key):
    cache.save_to_cache(key, [{"model": "a/one", "response": "A"}], [],
                        {"model": "a/one", "response": "Final"}, {"cache_hit": False})


def test_toggle(configure_cache):
    update, directory = configure_cache
    key = cache.get_cache_key("question")
    save(key)
    assert not directory.exists()
    update(cache={"enabled": True})
    save(key)
    assert (directory / f"{key}.json").exists(), "cache did not enable after reload"
    assert cache.get_cached_response(key)["stage3_result"]["response"] == "Final"
    update(cache={"enabled": False})
    assert cache.get_cached_response(key) is None
    other = cache.get_cache_key("other")
    save(other)
    assert not (directory / f"{other}.json").exists()


def test_ttl_reload(configure_cache):
    update, directory = configure_cache
    update(cache={"enabled": True, "ttl_seconds": 0})
    key = cache.get_cache_key("question")
    save(key)
    path = directory / f"{key}.json"
    assert path.exists(), "cache did not enable after reload"
    data = json.loads(path.read_text())
    data["_cached_at"] = time.time() - 3600
    path.write_text(json.dumps(data))
    assert cache.get_cached_response(key)
    update(cache={"ttl_seconds": 60})
    assert cache.get_cached_response(key) is None and not path.exists()


def test_verdict_dissent(configure_cache):
    keys = {cache.get_cache_key("question", verdict_type=v, include_dissent=d)
            for v in ("synthesis", "binary", "tie_breaker") for d in (False, True)}
    assert len(keys) == 6


def test_live_pool_and_settings(configure_cache):
    update, _ = configure_cache
    key = cache.get_cache_key("question")
    update(council={"models": ["a/one", "b/two", "c/four"]})
    assert cache.get_cache_key("question") != key
    key = cache.get_cache_key("question")
    update(council={"synthesis_mode": "debate"})
    assert cache.get_cache_key("question") != key
    key = cache.get_cache_key("question")
    update(evaluation={"rubric": {"enabled": True}})
    assert cache.get_cache_key("question") != key


def test_deterministic_key_and_corrupt_entry(configure_cache):
    update, directory = configure_cache
    update(cache={"enabled": True})
    key = cache.get_cache_key("question")
    assert key == cache.get_cache_key("question")
    assert key != cache.get_cache_key("other")
    save(key)
    path = directory / f"{key}.json"
    assert path.exists(), "cache did not enable after reload"
    path.write_text("{not-json")
    assert cache.get_cached_response(key) is None and not path.exists()

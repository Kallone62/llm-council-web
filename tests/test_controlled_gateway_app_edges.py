"""App boundary checks for native gateway exhaustion and Free isolation."""
import json
from collections import Counter

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import (
    CHAIRMAN, KEYS, PRIMARY, QUESTION, SECONDARY, TITLE,
    gateway_config, install_transport,
)


@pytest.mark.parametrize("stream", [False, True], ids=["rest", "sse"])
@pytest.mark.parametrize("scenario", ["exhausted", "free", "free-conflict"])
def test_gateway_app_failure_boundaries(gateway_config, monkeypatch, tmp_path, stream, scenario):
    from backend import config, main, storage
    from backend.free_profile import FREE_MODELS, safe_local_title

    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr(storage, "DATA_DIR", str(tmp_path / "conversations"))
    reload_config()
    with TestClient(main.app) as client:
        selected = client.put("/api/settings", json={"profile": "free"})
        assert selected.status_code == 200
        assert get_config().gateways.fallback.enabled is False
        if scenario == "exhausted":
            selected = client.put("/api/settings", json={"profile": "high"})
            assert selected.status_code == 200
            assert get_config().gateways.fallback.enabled is True
            calls = install_transport(monkeypatch, 503, secondary_status=503)
        else:
            calls = []

            async def transport(_transport, request):
                calls.append((str(request.url), json.loads(request.content)["model"]))
                assert request.headers["authorization"] == f"Bearer {KEYS[PRIMARY]}"
                return httpx.Response(503, json={"error": {"message": "Fixture unavailable"}})

            monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", transport)
            if scenario == "free-conflict":
                cfg = yaml.safe_load(gateway_config.read_text(encoding="utf-8"))
                cfg["gateways"]["fallback"]["enabled"] = True
                gateway_config.write_text(yaml.safe_dump(cfg), encoding="utf-8")
                reload_config()
                assert get_config().gateways.fallback.enabled is True
        created = client.post("/api/conversations", json={})
        assert created.status_code == 200
        cid = created.json()["id"]
        endpoint = f"/api/conversations/{cid}/message" + ("/stream" if stream else "")
        response = client.post(endpoint, json={"content": QUESTION})
        if scenario == "free-conflict":
            assert response.status_code == 409 and not calls
            assert storage.get_conversation(cid)["messages"] == []
            return
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            terminal = {e["type"]: e for e in events if e["type"] != "engine_progress"}
            assert "error" not in terminal
            result = {stage: terminal[f"{stage}_complete"]["data"] for stage in ("stage1", "stage2", "stage3")}
            result["metadata"] = terminal["complete"]["metadata"]
            assert terminal["stage2_complete"]["metadata"] == terminal["stage3_complete"]["metadata"] == result["metadata"]
            progress = [e["data"] for e in events if e["type"] == "engine_progress"]
            assert result["metadata"]["ui_runtime_progress"] == progress
            assert progress[-1]["state"] == "failed"
            assert progress[-1]["stage"] == ("stage3" if scenario == "exhausted" else "stage1")
            assert ("title_complete" in terminal) is (scenario == "exhausted")
        else:
            result = response.json()
        assert result["metadata"]["status"] == "failed"
        assert result["stage3"]["error_status"]
        if scenario == "exhausted":
            assert [c for c in calls if c[0] == "stage3"] == [
                ("stage3", CHAIRMAN, PRIMARY, 503), ("stage3", CHAIRMAN, SECONDARY, 503)
            ]
            assert len(result["stage1"]) == len(result["stage2"]) == 4
            assert result["metadata"]["usage"]["total"]["total_tokens"] == 144
            assert result["metadata"]["peer_review"]["effective_votes"] == 12
            expected_title = TITLE
        else:
            assert Counter(calls) == Counter((PRIMARY, model) for model in FREE_MODELS)
            assert result["stage1"] == result["stage2"] == []
            assert result["stage3"]["error_status"] == "no_responses"
            assert result["metadata"]["peer_review"]["state"] == "not_run"
            expected_title = safe_local_title(QUESTION)
        disk = storage.get_conversation(cid)
        assert disk["title"] == expected_title
        assert disk["messages"] == [{"role": "user", "content": QUESTION}, {"role": "assistant", **result}]
        assert client.get(f"/api/conversations/{cid}").json() == disk

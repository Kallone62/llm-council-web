"""Offline integration checks for the Karpathy UI -> Amiable engine boundary."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import main, storage
from llm_council.verdict import VerdictType


def _engine_result():
    return (
        [{"model": "provider/model-a", "response": "A"}],
        [
            {
                "model": "provider/reviewer",
                "ranking": "review",
                "parsed_ranking": {
                    "ranking": ["Response A"],
                    "scores": {"Response A": 9},
                    "rubric_scoring": False,
                },
            }
        ],
        {"model": "provider/chairman", "response": "final"},
        {
            "label_to_model": {
                "Response A": {"model": "provider/model-a", "display_index": 0}
            },
            "aggregate_rankings": [
                {
                    "model": "provider/model-a",
                    "borda_score": 1.0,
                    "average_position": 1.0,
                    "vote_count": 1,
                    "rank": 1,
                }
            ],
            "usage": {"total": {"total_tokens": 123}},
            "verdict": {
                "verdict_type": "binary",
                "verdict": "approved",
                "confidence": 0.91,
                "rationale": "Council agreement",
                "dissent": "Minor concern",
                "deadlocked": False,
                "borda_spread": 0.5,
            },
        },
    )


def test_direct_engine_call_preserves_modern_metadata(tmp_path, monkeypatch):
    storage.DATA_DIR = str(tmp_path / "conversations")
    observed = {}

    async def fake_run_full_council(query, **kwargs):
        observed["query"] = query
        observed.update(kwargs)
        return _engine_result()

    async def fake_title(_query):
        return "Decision Test"

    monkeypatch.setattr(main, "run_full_council", fake_run_full_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)

    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/message",
        json={
            "content": "Should we ship?",
            "verdict_type": "binary",
            "include_dissent": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert observed["query"] == "Should we ship?"
    assert observed["verdict_type"] is VerdictType.BINARY
    assert observed["include_dissent"] is True
    assert payload["stage2"][0]["parsed_ranking"]["scores"]["Response A"] == 9
    assert payload["metadata"]["verdict"]["verdict"] == "approved"
    assert payload["metadata"]["usage"]["total"]["total_tokens"] == 123

    stored = storage.get_conversation(conversation["id"])
    assistant = stored["messages"][-1]
    assert assistant["metadata"]["verdict"]["confidence"] == 0.91
    assert assistant["metadata"]["label_to_model"]["Response A"]["display_index"] == 0


def test_sse_is_only_an_envelope_around_full_engine_result(tmp_path, monkeypatch):
    storage.DATA_DIR = str(tmp_path / "conversations")

    async def fake_run_full_council(_query, **_kwargs):
        return _engine_result()

    async def fake_title(_query):
        return "Stream Test"

    monkeypatch.setattr(main, "run_full_council", fake_run_full_council)
    monkeypatch.setattr(main, "generate_conversation_title", fake_title)

    client = TestClient(main.app)
    conversation = client.post("/api/conversations", json={}).json()
    response = client.post(
        f"/api/conversations/{conversation['id']}/message/stream",
        json={"content": "Decide", "verdict_type": "binary", "include_dissent": True},
    )

    assert response.status_code == 200
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    assert [event["type"] for event in events] == [
        "stage1_start",
        "stage1_complete",
        "stage2_start",
        "stage2_complete",
        "stage3_start",
        "stage3_complete",
        "title_complete",
        "complete",
    ]
    stage2_event = next(event for event in events if event["type"] == "stage2_complete")
    assert stage2_event["data"][0]["parsed_ranking"]["ranking"] == ["Response A"]
    assert stage2_event["metadata"]["aggregate_rankings"][0]["borda_score"] == 1.0
    stage3_event = next(event for event in events if event["type"] == "stage3_complete")
    assert stage3_event["metadata"]["verdict"]["verdict"] == "approved"


def test_no_karpathy_council_adapter_file_remains():
    project_root = Path(__file__).resolve().parents[1]
    assert not (project_root / "backend" / "council.py").exists()
    assert not (project_root / "backend" / "openrouter.py").exists()


def test_settings_edit_amiable_yaml_directly(tmp_path, monkeypatch):
    from llm_council.unified_config import reload_config

    original_path = main.os.environ.get("LLM_COUNCIL_CONFIG")
    config_path = tmp_path / "llm_council.yaml"
    config_path.write_text(
        """tiers:\n  default: high\ncouncil:\n  models:\n    - openai/gpt-5.6-sol\n    - anthropic/claude-opus-5\n    - deepseek/deepseek-v4-pro-0813\n    - z-ai/glm-5.3\nmodel_intelligence:\n  enabled: false\n""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_COUNCIL_CONFIG", str(config_path))
    reload_config()

    client = TestClient(main.app)
    response = client.put(
        "/api/settings",
        json={
            "profile": "balanced",
            "use_profile_models": True,
            "models": [],
            "chairman": None,
            "synthesis_mode": "debate",
            "exclude_self_votes": True,
            "style_normalization": "auto",
            "max_reviewers": 2,
            "rubric_enabled": True,
            "safety_enabled": False,
            "bias_audit_enabled": True,
            "cache_enabled": True,
            "cache_ttl_seconds": 300,
            "timeout_multiplier": 1.5,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["profile"] == "balanced"
    assert payload["synthesis_mode"] == "debate"
    assert payload["style_normalization"] == "auto"
    assert payload["rubric_enabled"] is True

    import yaml

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert raw["tiers"]["default"] == "balanced"
    assert raw["council"]["models"] == payload["options"]["profiles"]["balanced"]["models"]
    assert raw["model_intelligence"]["enabled"] is False

    if original_path is None:
        monkeypatch.delenv("LLM_COUNCIL_CONFIG", raising=False)
    else:
        monkeypatch.setenv("LLM_COUNCIL_CONFIG", original_path)
    reload_config()


def test_settings_never_return_openrouter_secret(monkeypatch):
    secret = "test-openrouter-key-do-not-return"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)
    client = TestClient(main.app)
    response = client.get("/api/settings")
    assert response.status_code == 200
    assert response.json()["api_key_configured"] is True
    assert secret not in response.text

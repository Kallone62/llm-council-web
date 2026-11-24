"""Catalogue discovery tests; no real OpenRouter requests."""
import httpx
import pytest

from backend.free_model_catalog import eligible_models, fetch_catalogue, report, select_pool
from backend.free_profile import FREE_MODELS


def model(slug="alpha/model:free", **overrides):
    return {
        "id": slug,
        "pricing": {"prompt": "0", "completion": "0", "request": "0"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "context_length": 32768,
        **overrides,
    }


@pytest.mark.parametrize("entry", [
    model("alpha/model"),
    model("openrouter/free"),
    model("openrouter/router:free"),
    model("invalid:free"),
    model(pricing={"prompt": "0.001", "completion": "0"}),
    model(pricing={"prompt": "0", "completion": "0.001"}),
    model(pricing={"prompt": "0", "completion": "0", "request": "0.1"}),
    model(pricing={"prompt": "0"}),
    model(pricing={"prompt": None, "completion": "0"}),
    model(pricing={"prompt": "NaN", "completion": "0"}),
    model(pricing={"prompt": False, "completion": "0"}),
    model(architecture={"input_modalities": ["image"], "output_modalities": ["text"]}),
    model(architecture={"input_modalities": ["text"], "output_modalities": ["image"]}),
    model(context_length=4096),
    model(context_length="32768"),
    model(endpoints=["/api/v1/embeddings"]),
    None,
])
def test_rejects_ineligible_entries(entry):
    assert eligible_models({"data": [entry]}) == []


def test_deduplicates_and_accepts_zero_prices():
    entry = model(pricing={"prompt": "0.000", "completion": 0})
    assert eligible_models({"data": [entry, entry]}) == ["alpha/model:free"]


def test_existing_models_preferred_and_distinct_authors_first():
    first = FREE_MODELS[0]
    candidates = [first, "alpha/a:free", "alpha/b:free", "beta/c:free"]
    expected = [first, "alpha/a:free", "beta/c:free"]
    assert select_pool(candidates) == expected
    assert select_pool(list(reversed(candidates))) == expected


def test_uses_other_distinct_models_if_author_diversity_is_impossible():
    assert select_pool(["a/c:free", "a/b:free", "a/a:free"]) == [
        "a/a:free", "a/b:free", "a/c:free"
    ]


def test_insufficient_models_never_silently_selects_paid_or_duplicate_models():
    with pytest.raises(ValueError, match="Only 1"):
        select_pool(["a/b:free", "a/b:free"])
    data = report({"data": [model()]})
    assert data["proposed_pool"] == []
    assert data["inference_verified"] is False


def test_report_detects_missing_legacy_models():
    data = report({"data": [model(FREE_MODELS[0]), model("a/b:free"), model("b/c:free")]})
    assert data["existing_models"][FREE_MODELS[0]] is True
    assert data["existing_models"][FREE_MODELS[1]] is False
    assert data["proposed_pool"][0] == FREE_MODELS[0]


def test_catalogue_uses_only_metadata_get():
    calls = []
    def handler(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(200, json={"data": [model()]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert eligible_models(fetch_catalogue(client)) == ["alpha/model:free"]
    assert calls == [("GET", "https://openrouter.ai/api/v1/models")]


def test_catalogue_http_errors_propagate():
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(503)
    )) as client:
        with pytest.raises(httpx.HTTPStatusError):
            fetch_catalogue(client)


@pytest.mark.parametrize("document", [{}, {"data": None}, {"data": {}}, []])
def test_invalid_catalogue_is_not_treated_as_an_empty_success(document):
    with pytest.raises(ValueError, match="Invalid OpenRouter catalogue"):
        eligible_models(document)

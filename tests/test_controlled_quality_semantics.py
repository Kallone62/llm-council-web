"""Quality metadata is a heuristic, not a truth verdict; failed repair is unavailable."""
import asyncio
import json
import re
from collections import Counter
from copy import deepcopy

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import reload_config

from backend import engine_runtime
from test_controlled_gateway_fallback import CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config

QUESTION = 'ENG29: Compare the composition of the Moon.'
TITLE = 'Moon Composition'
# Deliberately false first answer, copied verbatim by the Chairman.
ANSWERS = dict(zip(MODELS, ('The Moon is made of cheese.', 'Silicate rock', 'Basalt regolith', 'Olivine mineral')))
CRITIQUE = 'Every archive option has been checked for durability and provenance.'
# All response token sets are disjoint, four returned reviews and ten words per critique.
# CSS = .4/3 + .4 + .2; DDI = .35 + .35 + .3*(10/50).
# Winner overlap mean(1,0), max(1,0,0,0); neither is a truth measurement.
EXPECTED = {'tier': 'core', 'core': {'consensus_strength': .733, 'deliberation_depth': .76,
            'synthesis_attribution': {'winner_alignment': .5, 'max_source_alignment': 1.0,
                                      'hallucination_risk': 0.0, 'grounded': True}}}


def install_provider(path, monkeypatch):
    config = yaml.safe_load(path.read_text(encoding='utf-8'))
    config['council']['exclude_self_votes'] = False
    path.write_text(yaml.safe_dump(config), encoding='utf-8')
    monkeypatch.setenv('LLM_COUNCIL_QUALITY_METRICS', 'true')
    monkeypatch.setenv('LLM_COUNCIL_QUALITY_TIER', 'core')
    reload_config()
    calls, ballots = [], {}

    async def handle(_transport, request):
        assert (request.method, str(request.url), request.headers.get('authorization')) == (
            'POST', PRIMARY, f'Bearer {KEYS[PRIMARY]}')
        body = json.loads(request.content)
        model, prompt = body['model'], body['messages'][-1]['content']
        assert QUESTION in prompt
        if prompt == QUESTION:
            stage, answer = 'stage1', ANSWERS[model]
        elif '<responses_to_evaluate>' in prompt:
            stage = 'stage2'
            labels = {content: 'Response ' + label for label, content in re.findall(
                r'<candidate_response id="([A-D])">\n(.*?)\n</candidate_response>', prompt, re.S)}
            assert set(labels) == set(ANSWERS.values())
            answer = CRITIQUE + '\nFINAL RANKING:\n' + '\n'.join(
                f'{i}. {labels[ANSWERS[m]]}' for i, m in enumerate(MODELS, 1))
            ballots[model] = answer
        elif prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = 'stage3', ANSWERS[CHAIRMAN]
            assert model == CHAIRMAN and prompt.startswith('You are the Chairman of an LLM Council.')
            assert all(value in prompt for value in ANSWERS.values())
            assert all(raw in prompt for raw in ballots.values())
        calls.append((stage, model))
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle)
    return calls, ballots


def instrument_repair(monkeypatch, fail):
    actual = engine_runtime.calculate_quality_metrics
    invocations = []

    def calculate(**kwargs):
        assert kwargs['stage1_responses'] == {m: {'content': v} for m, v in ANSWERS.items()}
        assert kwargs['stage3_synthesis'] == ANSWERS[CHAIRMAN]
        assert kwargs['aggregate_rankings'] == list(zip(MODELS, (1.0, 2.0, 3.0, 4.0)))
        assert len(kwargs['stage2_rankings']) == 4
        assert all(review['content'] == review['ranking'] for review in kwargs['stage2_rankings'])
        invocations.append(kwargs)
        if fail:
            raise RuntimeError('Controlled quality projection failure')
        return actual(**kwargs)

    monkeypatch.setattr(engine_runtime, 'calculate_quality_metrics', calculate)
    return invocations


def verify(payload, observation, failed, title):
    calls, ballots = observation
    expected_calls = [(stage, model) for stage in ('stage1', 'stage2') for model in MODELS]
    expected_calls.append(('stage3', CHAIRMAN))
    assert Counter(calls) == Counter(expected_calls + ([('title', TITLE_MODEL)] if title else []))
    assert {r['model']: r['response'] for r in payload['stage1']} == ANSWERS
    assert len(payload['stage2']) == 4
    metadata = payload['metadata']
    labels = {entry['model']: label for label, entry in metadata['label_to_model'].items()}
    assert set(labels) == set(MODELS)
    for review in payload['stage2']:
        assert review['ranking'] == ballots[review['model']] and 'content' not in review
        assert review['parsed_ranking']['ranking'] == [labels[m] for m in MODELS]
    assert payload['stage3'] == {'model': CHAIRMAN, 'response': ANSWERS[CHAIRMAN]}
    ranks = metadata['aggregate_rankings']
    assert [r['model'] for r in ranks] == list(MODELS)
    assert [r['average_position'] for r in ranks] == [1.0, 2.0, 3.0, 4.0]
    assert [r['borda_score'] for r in ranks] == [1.0, .667, .333, 0.0]
    assert all(r['vote_count'] == 4 and r['self_votes_excluded'] is False for r in ranks)
    assert metadata['peer_review']['state'] == 'completed'
    assert metadata['peer_review']['effective_votes'] == 16
    assert metadata.get('status') not in ('failed', 'partial')
    assert metadata['usage']['total']['total_tokens'] == 162
    assert metadata['quality_metrics'] == (None if failed else EXPECTED)
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize('fail', [False, True], ids=['success', 'repair-error'])
def test_native_quality_repair_semantics(gateway_config, monkeypatch, fail):
    observation = install_provider(gateway_config, monkeypatch)
    stage1, stage2, stage3, raw = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    # The pinned engine's raw extraction is known to lose actual synthesis and critique text.
    assert raw['quality_metrics']['core']['synthesis_attribution'] == {
        'winner_alignment': 0.0, 'max_source_alignment': 0.0, 'hallucination_risk': 1.0, 'grounded': False}
    assert raw['quality_metrics']['core']['deliberation_depth'] == .7
    metadata = engine_runtime.annotate_peer_review(stage1, stage2, stage3, raw)
    before = deepcopy((stage1, stage2, stage3, metadata))
    invoked = instrument_repair(monkeypatch, fail)
    corrected = engine_runtime.repair_quality_metrics(stage1, stage2, stage3, metadata)
    assert len(invoked) == 1
    assert (stage1, stage2, stage3, metadata) == before
    assert {k: v for k, v in corrected.items() if k != 'quality_metrics'} == {
        k: v for k, v in metadata.items() if k != 'quality_metrics'}
    verify(dict(stage1=stage1, stage2=stage2, stage3=stage3, metadata=corrected), observation, fail, False)


@pytest.mark.parametrize('missing', ['responses', 'positions'])
def test_quality_repair_insufficient_inputs(gateway_config, monkeypatch, missing):
    install_provider(gateway_config, monkeypatch)
    stage1, stage2, stage3, metadata = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    metadata = engine_runtime.annotate_peer_review(stage1, stage2, stage3, metadata)
    # A cached or incomplete projection can retain stale native metrics. Do not reuse them.
    if missing == 'responses':
        stage1 = []
    else:
        metadata = {**metadata, 'aggregate_rankings': metadata['aggregate_rankings'][:1]}
    before = deepcopy(metadata)
    invoked = instrument_repair(monkeypatch, False)
    corrected = engine_runtime.repair_quality_metrics(stage1, stage2, stage3, metadata)
    assert not invoked
    assert corrected == {**before, 'quality_metrics': None}
    assert metadata == before


@pytest.mark.parametrize('fail', [False, True], ids=['success', 'repair-error'])
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_quality_repair_semantics_api(gateway_config, monkeypatch, tmp_path, fail, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    observation = install_provider(gateway_config, monkeypatch)
    invoked = instrument_repair(monkeypatch, fail)
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post('/api/conversations', json={})
        assert created.status_code == 200
        cid = created.json()['id']
        endpoint = f'/api/conversations/{cid}/message' + ('/stream' if stream else '')
        payload = decode_response(client.post(endpoint, json={'content': QUESTION}), stream)
        assert len(invoked) == 1
        verify(payload, observation, fail, True)
        disk = storage.get_conversation(cid)
        assert disk['title'] == TITLE
        assert disk['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **payload}]
        assert client.get(f'/api/conversations/{cid}').json() == disk

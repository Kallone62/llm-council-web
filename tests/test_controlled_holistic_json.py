"""Nonfinite model-emitted ballot numbers must never leak into structured JSON."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import reload_config

from test_controlled_gateway_fallback import (
    CHAIRMAN, MODELS, TITLE_MODEL, decode_openrouter_review_request, gateway_config,
)

QUESTION = 'ENG30: Which verification method should be used?'
TITLE = 'Verification Method'
FINAL = 'Verify the checksum.'
ANSWERS = {model: f'Verification method {i}: check the archive.' for i, model in enumerate(MODELS, 1)}
# Independent expected parsed values, not derived from the native decoder.
SCORES = {
    'finite': (10, '7.5', 4.25, 1),
    'constants': (10, None, None, None),
    'overflow': (10, 7.5, None, None),
    'nested': (10, 7.5, {'bad': [None, None]}, None),
}
AVERAGES = {
    'finite': (1.0, .75, .425, .1),
    'constants': (1.0, None, None, None),
    'overflow': (1.0, .75, None, None),
    'nested': (1.0, .75, None, None),
}
WIRE = {
    'finite': ('10', '"7.5"', '4.25', '1'),
    'constants': ('10', 'NaN', 'Infinity', '-Infinity'),
    'overflow': ('10', '7.5', '1e400', '-1e400'),
    'nested': ('10', '7.5', '{"bad":[NaN,1e400]}', '-Infinity'),
}


def ballot(labels, case, fenced, rubric):
    ranking = json.dumps([labels[m] for m in MODELS])
    scores = ','.join(f'{json.dumps(labels[m])}:{value}' for m, value in zip(MODELS, WIRE[case]))
    # An invalid rubric should still use the holistic decoder without leaking nonfinite values.
    extra = ',"evaluations":{}' if rubric else ''
    wire = '{"ranking":' + ranking + ',"scores":{' + scores + '}' + extra + '}'
    return 'Review follows.\n```json\n' + wire + '\n```' if fenced else wire


def install_provider(path, monkeypatch, case, fenced, rubric):
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    document['evaluation']['rubric']['enabled'] = rubric
    path.write_text(yaml.safe_dump(document), encoding='utf-8')
    monkeypatch.setenv('LLM_COUNCIL_QUALITY_METRICS', 'false')
    reload_config()
    calls, labels, raw = [], {}, {}

    async def handle(_transport, request):
        model, prompt = decode_openrouter_review_request(request, QUESTION)
        if prompt == QUESTION:
            stage, answer = 'stage1', ANSWERS[model]
        elif '<responses_to_evaluate>' in prompt:
            stage = 'stage2'
            observed = {MODELS[int(index)-1]: 'Response ' + label for label, index in re.findall(
                r'<candidate_response id="([A-D])">\nVerification method ([1-4]): check the archive\.\n</candidate_response>', prompt)}
            assert set(observed) == set(MODELS) and len(set(observed.values())) == 4
            assert not labels or labels == observed
            labels.update(observed)
            answer = raw[model] = ballot(labels, case, fenced, rubric)
        elif prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = 'stage3', FINAL
            assert model == CHAIRMAN and prompt.startswith('You are the Chairman of an LLM Council.')
            assert all(value in prompt for value in ANSWERS.values())
            assert all(value in prompt for value in raw.values())
        calls.append((stage, model))
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle)
    return calls, labels, raw


def verify(payload, observation, case, rubric, api):
    calls, labels, raw = observation
    expected = [(stage, m) for stage in ('stage1', 'stage2') for m in MODELS] + [('stage3', CHAIRMAN)]
    assert Counter(calls) == Counter(expected + ([('title', TITLE_MODEL)] if api else []))
    assert {r['model']: r['response'] for r in payload['stage1']} == ANSWERS
    assert len(payload['stage2']) == 4
    metadata = payload['metadata']
    assert {entry['model']: label for label, entry in metadata['label_to_model'].items()} == labels
    for review in payload['stage2']:
        assert review['ranking'] == raw[review['model']]
        parsed = review['parsed_ranking']
        assert parsed['ranking'] == [labels[m] for m in MODELS]
        assert parsed['scores'] == dict(zip((labels[m] for m in MODELS), SCORES[case]))
        assert 'evaluations' not in parsed
        if rubric:
            assert parsed['rubric_scoring'] is False
        else:
            assert 'rubric_scoring' not in parsed
    ranks = metadata['aggregate_rankings']
    assert [(r['model'], r['average_position'], r['borda_score']) for r in ranks] == list(
        zip(MODELS, (1.0, 2.0, 3.0, 4.0), (1.0, .667, .333, 0.0)))
    assert [r['average_score'] for r in ranks] == list(AVERAGES[case])
    assert all(r['vote_count'] == 3 and r['self_votes_excluded'] is True for r in ranks)
    assert payload['stage3'] == {'model': CHAIRMAN, 'response': FINAL}
    assert metadata['usage']['total']['total_tokens'] == 162
    assert metadata.get('status') not in ('partial', 'failed')
    if api:
        assert metadata['peer_review']['state'] == 'completed'
        assert metadata['peer_review']['effective_votes'] == 12
    # Strict JSON rejects nonfinite values even deep inside invalid ballot entries.
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


@pytest.mark.parametrize('case', SCORES)
@pytest.mark.parametrize('fenced', [False, True], ids=['raw', 'fenced'])
@pytest.mark.parametrize('rubric', [False, True], ids=['holistic', 'rubric-fallback'])
def test_native_holistic_json(gateway_config, monkeypatch, case, fenced, rubric):
    observation = install_provider(gateway_config, monkeypatch, case, fenced, rubric)
    result = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    verify(dict(zip(('stage1', 'stage2', 'stage3', 'metadata'), result)), observation, case, rubric, False)


@pytest.mark.parametrize('fenced', [False, True], ids=['raw', 'fenced'])
def test_nested_invalid_ranking_is_json_safe(fenced):
    wire = '{"ranking":["Response A",{"bad":[NaN,1e400]},-Infinity,"Response B"],"scores":{"Response A":8}}'
    text = '```json\n' + wire + '\n```' if fenced else wire
    parsed = council.parse_ranking_from_text(text)
    assert parsed == {'ranking': ['Response A', {'bad': [None, None]}, None, 'Response B'],
                      'scores': {'Response A': 8}}
    assert json.loads(json.dumps(parsed, allow_nan=False)) == parsed


@pytest.mark.parametrize('case', SCORES)
@pytest.mark.parametrize('fenced', [False, True], ids=['raw', 'fenced'])
@pytest.mark.parametrize('rubric', [False, True], ids=['holistic', 'rubric-fallback'])
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_holistic_json_api(gateway_config, monkeypatch, tmp_path, case, fenced, rubric, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    observation = install_provider(gateway_config, monkeypatch, case, fenced, rubric)
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post('/api/conversations', json={})
        assert created.status_code == 200
        cid = created.json()['id']
        path = f'/api/conversations/{cid}/message' + ('/stream' if stream else '')
        payload = decode_response(client.post(path, json={'content': QUESTION}), stream, expected_title=TITLE)
        verify(payload, observation, case, rubric, True)
        saved = storage.get_conversation(cid)
        assert saved['title'] == TITLE
        assert saved['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **payload}]
        assert client.get(f'/api/conversations/{cid}').json() == saved

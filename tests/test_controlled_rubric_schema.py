"""Malformed rubric ballots must not crash Council or invent dimension scores."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config
from test_controlled_profiles import TITLE, decode_response

QUESTION = 'ENG28: Should we verify the archive checksum?'
FINAL = 'Verify the archive checksum.'
ANSWERS = {m: f'Archive plan {i}: verify checksum.' for i, m in enumerate(MODELS, 1)}
DIMENSIONS = ('accuracy', 'relevance', 'completeness', 'conciseness', 'clarity')
VALUES = (4, 6, 8, 10)
CASES = ('valid', 'decimal', 'holistic', 'missing-dimension', 'missing-evaluation',
         'extra-evaluation', 'empty-evaluations', 'null-evaluations', 'list-evaluations',
         'string-evaluations', 'null-entry', 'list-entry', 'bool', 'null', 'string',
         'object', 'array', 'below-range', 'above-range', 'nan', 'infinity',
         'malformed-json', 'explicit-scores', 'nan-notes')


def review_payload(labels, case):
    ranked = [labels[m] for m in reversed(MODELS)]
    evaluations = {labels[m]: dict.fromkeys(DIMENSIONS, v) for m, v in zip(MODELS, VALUES)}
    data = {'ranking': ranked, 'evaluations': evaluations}
    first, second, third, _ = (labels[m] for m in MODELS)
    if case == 'decimal':
        evaluations[third] = dict.fromkeys(DIMENSIONS, 8.5)
    elif case == 'holistic':
        data.pop('evaluations')
        data['scores'] = dict(zip((labels[m] for m in MODELS), (1, 2, 3, 4)))
    elif case == 'missing-dimension' or case == 'explicit-scores':
        evaluations[first].pop('accuracy')
        if case == 'explicit-scores':
            data['scores'] = dict(zip((labels[m] for m in MODELS), (1, 2, 3, 4)))
    elif case == 'missing-evaluation':
        evaluations.pop(second)
    elif case == 'extra-evaluation':
        evaluations['Response Z'] = dict.fromkeys(DIMENSIONS, 9)
    elif case in ('empty-evaluations', 'null-evaluations', 'list-evaluations', 'string-evaluations'):
        data['evaluations'] = {'empty-evaluations': {}, 'null-evaluations': None,
                               'list-evaluations': [], 'string-evaluations': 'bad'}[case]
    elif case in ('null-entry', 'list-entry'):
        evaluations[first] = None if case == 'null-entry' else []
    elif case == 'nan-notes':
        evaluations[first]['notes'] = float('nan')
    elif case == 'ranking-null':
        data['ranking'] = None
    elif case == 'ranking-duplicate':
        data['ranking'] = ranked + ranked[:1]
    elif case == 'ranking-unknown':
        data['ranking'] = ranked + ['Response Z']
        evaluations['Response Z'] = dict.fromkeys(DIMENSIONS, 9)
    elif case == 'ranking-nested':
        data['ranking'] = ranked + [{}]
    elif case == 'malformed-json':
        legacy = '\n'.join(f'{i}. {label}' for i, label in enumerate(ranked, 1))
        return '```json\n{"evaluations": [}\n```\nFINAL RANKING:\n' + legacy
    elif case != 'valid':
        evaluations[first]['accuracy'] = {'bool': True, 'null': None, 'string': '8',
            'object': {}, 'array': [], 'below-range': 0, 'above-range': 11,
            'nan': float('nan'), 'infinity': float('inf')}[case]
    return '```json\n' + json.dumps(data) + '\n```'


def setup_case(path, monkeypatch, case):
    for name in ('RUBRIC_SCORING_ENABLED', 'ACCURACY_CEILING_ENABLED', 'RUBRIC_RANDOMIZE_DIMENSION_ORDER'):
        monkeypatch.delenv(name, raising=False)
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    document['evaluation']['rubric'] = {'enabled': True, 'accuracy_ceiling_enabled': True,
                                         'randomize_dimension_order': False}
    path.write_text(yaml.safe_dump(document), encoding='utf-8')
    reload_config()
    calls, labels, raw, chairman_prompts = [], {}, {}, []

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
            observed = {MODELS[int(index)-1]: 'Response ' + label for label, index in re.findall(
                r'<candidate_response id="([A-D])">\nArchive plan ([1-4]): verify checksum\.\n</candidate_response>', prompt)}
            assert len(observed) == 4 and set(observed.values()) == set('Response ' + l for l in 'ABCD')
            assert not labels or labels == observed
            labels.update(observed)
            assert 'score ALL FIVE dimensions (1-10)' in prompt
            answer = review_payload(labels, case if model == MODELS[-1] else 'valid')
            raw[model] = answer
        elif prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = 'stage3', FINAL
            assert model == CHAIRMAN and prompt.startswith('You are the Chairman of an LLM Council.')
            assert all(value in prompt for value in ANSWERS.values())
            assert all(value in prompt for value in raw.values())
            chairman_prompts.append(prompt)
        calls.append((stage, model))
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle)
    return calls, labels, raw, chairman_prompts


def verify(case, observation, stage1, stage2, stage3, metadata, title):
    calls, labels, raw, prompts = observation
    expected_calls = [(stage, model) for stage in ('stage1', 'stage2') for model in MODELS] + [('stage3', CHAIRMAN)]
    assert Counter(calls) == Counter(expected_calls + ([('title', TITLE_MODEL)] if title else []))
    assert [stage for stage, _ in calls if stage != 'title'] == ['stage1']*4 + ['stage2']*4 + ['stage3']
    assert {r['model']: r['response'] for r in stage1} == ANSWERS
    assert len(stage2) == 4 and {r['model'] for r in stage2} == set(MODELS)
    assert {label: value['model'] for label, value in metadata['label_to_model'].items()} == {v: k for k, v in labels.items()}
    for review in stage2:
        model, parsed = review['model'], review['parsed_ranking']
        assert review['ranking'] == raw[model]
        assert parsed['ranking'] == [labels[m] for m in reversed(MODELS)]
        current = case if model == MODELS[-1] else 'valid'
        valid = current in ('valid', 'decimal')
        assert parsed['rubric_scoring'] is valid
        if valid:
            expected = (4, 6, 8.5, 10) if current == 'decimal' else VALUES
            assert parsed['scores'] == dict(zip((labels[m] for m in MODELS), expected))
            assert parsed['evaluations'] == {labels[m]: dict.fromkeys(DIMENSIONS, v) for m, v in zip(MODELS, expected)}
        else:
            assert 'evaluations' not in parsed
            assert parsed['scores'] == (dict(zip((labels[m] for m in MODELS), (1, 2, 3, 4)))
                                        if current in ('holistic', 'explicit-scores') else {})
    ranks = metadata['aggregate_rankings']
    assert [r['model'] for r in ranks] == list(reversed(MODELS))
    assert [r['borda_score'] for r in ranks] == [1.0, .667, .333, 0.0]
    assert [r['average_position'] for r in ranks] == [1.0, 2.0, 3.0, 4.0]
    assert all(r['vote_count'] == 3 and r['self_votes_excluded'] is True for r in ranks)
    expected = (.3, .467, .633, 1.0) if case in ('holistic', 'explicit-scores') else (
        (.4, .6, .817, 1.0) if case == 'decimal' else (.4, .6, .8, 1.0))
    assert {r['model']: r['average_score'] for r in ranks} == dict(zip(MODELS, expected))
    assert len(prompts) == 1
    for i, model in enumerate(reversed(MODELS), 1):
        assert f'#{i}. {model} (avg score: {expected[MODELS.index(model)]}, votes: 3)' in prompts[0]
    assert stage3 == {'model': CHAIRMAN, 'response': FINAL}
    assert metadata.get('status') not in ('failed', 'partial')
    assert metadata['usage']['total']['total_tokens'] == 162
    if title:
        assert metadata['peer_review']['state'] == 'completed'
        assert metadata['peer_review']['effective_votes'] == 12
    json.dumps((stage1, stage2, stage3, metadata), allow_nan=False)


@pytest.mark.parametrize('case', CASES)
def test_native_rubric_schema(gateway_config, monkeypatch, case):
    observation = setup_case(gateway_config, monkeypatch, case)
    verify(case, observation, *asyncio.run(council.run_full_council(QUESTION, bypass_cache=True)), False)


@pytest.mark.parametrize('case', ('ranking-null', 'ranking-duplicate', 'ranking-unknown', 'ranking-nested'))
def test_native_rubric_schema_invalid_ranking(gateway_config, monkeypatch, case):
    calls, labels, raw, _ = setup_case(gateway_config, monkeypatch, case)
    stage1 = [{'model': model, 'response': ANSWERS[model]} for model in MODELS]
    reviews, mapping, usage = asyncio.run(council.stage2_collect_rankings(QUESTION, stage1))
    assert Counter(calls) == Counter(('stage2', model) for model in MODELS)
    assert {label: value['model'] for label, value in mapping.items()} == {v: k for k, v in labels.items()}
    assert len(reviews) == 4
    for review in reviews:
        model, parsed = review['model'], review['parsed_ranking']
        assert review['ranking'] == raw[model]
        assert parsed['rubric_scoring'] is (model != MODELS[-1])
        if model == MODELS[-1]:
            assert 'evaluations' not in parsed and parsed['scores'] == {}
            # Preserve the native holistic parser's raw ballot; aggregation sanitizes it.
            ranked = [labels[m] for m in reversed(MODELS)]
            expected = {'ranking-null': [], 'ranking-duplicate': ranked + ranked[:1],
                        'ranking-unknown': ranked + ['Response Z'], 'ranking-nested': ranked + [{}]}
            assert parsed['ranking'] == expected[case]
        else:
            assert parsed['scores'] == dict(zip((labels[m] for m in MODELS), VALUES))
    assert usage['total_tokens'] == 72
    json.dumps(reviews, allow_nan=False)


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_rubric_schema_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage

    observation = setup_case(gateway_config, monkeypatch, case)
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        settings = client.put('/api/settings', json={'profile': 'high', 'rubric_enabled': True,
                              'exclude_self_votes': True, 'style_normalization': 'off'})
        assert settings.status_code == 200 and settings.json()['rubric_enabled'] is True
        reload_config()
        assert get_config().evaluation.rubric.enabled is True
        assert not observation[0]
        cid = client.post('/api/conversations', json={}).json()['id']
        endpoint = f'/api/conversations/{cid}/message' + ('/stream' if stream else '')
        payload = decode_response(client.post(endpoint, json={'content': QUESTION}), stream)
        verify(case, observation, payload['stage1'], payload['stage2'], payload['stage3'], payload['metadata'], True)
        disk = storage.get_conversation(cid)
        assert disk['title'] == TITLE
        assert disk['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **payload}]
        assert client.get(f'/api/conversations/{cid}').json() == disk

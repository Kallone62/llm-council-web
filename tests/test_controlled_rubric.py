"""Native rubric prompt, parsed dimensions, ceiling, aggregate and persistence."""
import asyncio
import json
import re
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council
from llm_council.unified_config import get_config, reload_config

from test_controlled_gateway_fallback import (
    CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config,
)

QUESTION = 'ENG20: Which archive retention approach is preferable?'
TITLE = 'Archive Retention Decision'
FINAL = 'Retain and verify the archive.'
ANSWERS = {model: f'Archive approach {i}: verify the checksum.' for i, model in enumerate(MODELS, 1)}
DIMENSIONS = ('accuracy', 'relevance', 'completeness', 'conciseness', 'clarity')
WEIGHTS = dict(zip(DIMENSIONS, (.35, .10, .20, .15, .20)))
CUSTOM = dict(zip(DIMENSIONS, (.50, .10, .15, .10, .15)))
EVALUATIONS = {model: dict(zip(DIMENSIONS, scores), notes=f'Assessment {i}')
               for i, (model, scores) in enumerate(zip(MODELS, (
                   (4, 10, 10, 10, 10), (5, 10, 10, 10, 10),
                   (6, 10, 10, 10, 10), (7, 8, 9, 6, 5))), 1)}
# Independent fixed oracles: base .35*a+.10*r+.20*c+.15*n+.20*l;
# accuracy 4 caps at 4; 5 and 6 cap at 7; 7 has no cap.
# Custom weights produce 7,7.5,8,7 before ceiling.
EXPECTED = {
    'off': (1, 2, 3, 4),
    'ceiling': (4.0, 7.0, 7.0, 6.95),
    'uncapped': (7.9, 8.25, 8.6, 6.95),
    'custom': (7.0, 7.5, 8.0, 7.0),
    'mixed': (4.0, 7.0, 7.0, 6.95),
    'rank-conflict': (4.0, 7.0, 7.0, 6.95),
}


def order(case):
    return (MODELS[0], MODELS[3], MODELS[2], MODELS[1]) if case == 'rank-conflict' else tuple(reversed(MODELS))


def configure(path, monkeypatch, case):
    for name in ('RUBRIC_SCORING_ENABLED', 'ACCURACY_CEILING_ENABLED', 'RUBRIC_RANDOMIZE_DIMENSION_ORDER'):
        monkeypatch.delenv(name, raising=False)
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    document['evaluation']['rubric'] = {'enabled': case != 'off',
        'accuracy_ceiling_enabled': case not in ('uncapped', 'custom'),
        'randomize_dimension_order': False, 'weights': CUSTOM if case == 'custom' else WEIGHTS}
    path.write_text(yaml.safe_dump(document), encoding='utf-8')
    reload_config()


def check_config(case):
    rubric = get_config().evaluation.rubric
    assert rubric.enabled is (case != 'off')
    assert rubric.accuracy_ceiling_enabled is (case not in ('uncapped', 'custom'))
    assert rubric.weights == (CUSTOM if case == 'custom' else WEIGHTS)


def ballot(case, model, labels):
    ranked = [labels[m] for m in order(case)]
    holistic = case == 'off' or (case == 'mixed' and model == MODELS[-1])
    if holistic:
        return {'ranking': ranked, 'scores': dict(zip((labels[m] for m in MODELS), EXPECTED['off']))}
    return {'ranking': ranked, 'evaluations': {labels[m]: EVALUATIONS[m] for m in MODELS}}


def install_provider(monkeypatch, case):
    calls, sent, review_labels, chairman_prompts = [], {}, {}, []

    async def transport(_transport, request):
        assert request.method == 'POST' and str(request.url) == PRIMARY
        assert request.headers['authorization'] == f'Bearer {KEYS[PRIMARY]}'
        body = json.loads(request.content)
        model, prompt = body['model'], body['messages'][-1]['content']
        assert QUESTION in prompt
        if prompt == QUESTION:
            stage, answer = 'stage1', ANSWERS[model]
        elif '<responses_to_evaluate>' in prompt:
            stage = 'stage2'
            assert model in MODELS and body['tools'] == [] and body['tool_choice'] == 'none'
            observed = {MODELS[int(index)-1]: 'Response ' + label for label, index in re.findall(
                r'<candidate_response id="([A-D])">\nArchive approach ([1-4]): verify the checksum\.\n</candidate_response>', prompt)}
            assert set(observed) == set(MODELS) and len(set(observed.values())) == 4
            if review_labels:
                assert review_labels == observed
            review_labels.update(observed)
            if case == 'off':
                assert 'EVALUATION RUBRIC' not in prompt and '"scores": {' in prompt
            else:
                assert 'EVALUATION RUBRIC - Score each dimension 1-10:' in prompt
                assert 'score ALL FIVE dimensions (1-10)' in prompt and '"evaluations": {' in prompt
                weights = CUSTOM if case == 'custom' else WEIGHTS
                for position, dimension in enumerate(DIMENSIONS, 1):
                    assert f'{position}. **{dimension.upper()}** ({int(weights[dimension]*100)}% of final score)' in prompt
                    assert prompt.count(f'"{dimension}": <1-10>') == 2
            data = ballot(case, model, observed)
            raw = json.dumps(data)
            answer = raw if model == MODELS[1] else 'Reviewer assessment.\n```json\n' + raw + '\n```'
            sent[model] = (data, answer)
        elif prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = 'stage3', FINAL
            assert model == CHAIRMAN and prompt.startswith('You are the Chairman of an LLM Council.')
            assert all(f'Model: {m}\nResponse: {value}' in prompt for m, value in ANSWERS.items())
            assert all(f'Model: {m}\nRanking: {raw}' in prompt for m, (_, raw) in sent.items())
            chairman_prompts.append(prompt)
        calls.append((stage, model))
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
    return calls, sent, review_labels, chairman_prompts


def verify(case, observation, stage1, stage2, stage3, metadata, title):
    calls, sent, labels, chairman_prompts = observation
    expected_calls = [(stage, model) for stage in ('stage1', 'stage2') for model in MODELS] + [('stage3', CHAIRMAN)]
    if title:
        expected_calls.append(('title', TITLE_MODEL))
    assert Counter(calls) == Counter(expected_calls)
    assert [stage for stage, _ in calls if stage != 'title'] == ['stage1']*4 + ['stage2']*4 + ['stage3']
    assert {r['model']: r['response'] for r in stage1} == ANSWERS
    assert len(stage2) == 4 and set(sent) == set(MODELS)
    assert {label: entry['model'] for label, entry in metadata['label_to_model'].items()} == {v: k for k, v in labels.items()}
    for review in stage2:
        model = review['model']
        data, raw = sent[model]
        parsed = review['parsed_ranking']
        assert review['ranking'] == raw and parsed['ranking'] == data['ranking']
        if 'evaluations' in data:
            assert parsed['rubric_scoring'] is True and parsed['evaluations'] == data['evaluations']
            assert parsed['scores'] == dict(zip((labels[m] for m in MODELS), EXPECTED[case]))
        else:
            assert parsed['scores'] == data['scores'] and 'evaluations' not in parsed
            if case == 'mixed':
                assert parsed['rubric_scoring'] is False
            else:
                assert 'rubric_scoring' not in parsed
    assert stage3 == {'model': CHAIRMAN, 'response': FINAL}
    ranks = metadata['aggregate_rankings']
    assert [r['model'] for r in ranks] == list(order(case))
    assert [r['borda_score'] for r in ranks] == [1.0, .667, .333, 0.0]
    assert [r['average_position'] for r in ranks] == [1.0, 2.0, 3.0, 4.0]
    assert all(r['vote_count'] == 3 and r['self_votes_excluded'] is True for r in ranks)
    expected_scores = (.3, .533, .567, .695) if case == 'mixed' else tuple(round(v/10, 3) for v in EXPECTED[case])
    assert {r['model']: r['average_score'] for r in ranks} == dict(zip(MODELS, expected_scores))
    assert len(chairman_prompts) == 1
    for index, model in enumerate(order(case), 1):
        score = expected_scores[MODELS.index(model)]
        assert f'#{index}. {model} (avg score: {score}, votes: 3)' in chairman_prompts[0]
    assert metadata.get('status') not in ('failed', 'partial')
    assert metadata['usage']['total']['total_tokens'] == 162
    if title:
        assert metadata['peer_review']['state'] == 'completed'
        assert metadata['peer_review']['effective_votes'] == 12


@pytest.mark.parametrize('case', EXPECTED)
def test_native_rubric(gateway_config, monkeypatch, case):
    configure(gateway_config, monkeypatch, case)
    check_config(case)
    observation = install_provider(monkeypatch, case)
    result = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    verify(case, observation, *result, False)


@pytest.mark.parametrize('case', EXPECTED)
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_rubric_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    from test_controlled_profiles import decode_response

    configure(gateway_config, monkeypatch, case)
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    assert main.run_full_council is council.run_full_council
    observation = install_provider(monkeypatch, case)
    with TestClient(main.app) as client:
        for enabled in (case == 'off', case != 'off'):
            selected = client.put('/api/settings', json={'profile': 'high', 'rubric_enabled': enabled,
                'exclude_self_votes': True, 'style_normalization': 'off'})
            assert selected.status_code == 200 and selected.json()['rubric_enabled'] is enabled
        saved = yaml.safe_load(gateway_config.read_text(encoding='utf-8'))['evaluation']['rubric']
        assert saved['enabled'] is (case != 'off')
        assert saved['accuracy_ceiling_enabled'] is (case not in ('uncapped', 'custom'))
        assert saved['weights'] == (CUSTOM if case == 'custom' else WEIGHTS)
        reload_config()
        check_config(case)
        assert client.get('/api/settings').json()['rubric_enabled'] is (case != 'off')
        assert not observation[0]
        created = client.post('/api/conversations', json={})
        assert created.status_code == 200
        cid = created.json()['id']
        endpoint = f'/api/conversations/{cid}/message' + ('/stream' if stream else '')
        result = decode_response(client.post(endpoint, json={'content': QUESTION}), stream)
        verify(case, observation, result['stage1'], result['stage2'], result['stage3'], result['metadata'], True)
        disk = storage.get_conversation(cid)
        assert disk['title'] == TITLE
        assert disk['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **result}]
        assert client.get(f'/api/conversations/{cid}').json() == disk

"""Native core quality metrics: fixed numerical oracles, actual provider HTTP."""
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

from test_controlled_gateway_fallback import (
    CHAIRMAN, KEYS, MODELS, PRIMARY, TITLE_MODEL, gateway_config,
)

QUESTION = 'ENG19: Compare archive retention approaches.'
TITLE = 'Archive Retention'
ANSWERS = dict(zip(MODELS, ('alpha beta gamma delta epsilon',
                            'alpha beta gamma theta iota', 'kappa lambda', 'mu nu')))
CRITIQUE = 'Evidence supports durable archive retention with checksums and clear provenance.'
# DDI: pairwise dissimilarities 4/7,1,1,1,1,1 => 13/14; 10 words => .2.
# Full legacy: .35*(13/14)+.35+.30*.2=.735.
# Compact fenced JSON adds 11 whitespace tokens in native richness; .801.
# Two returned partial ballots: .325+.175+.06=.560.
# CSS positions 1,2,3,4 => .4/3+.4+.2=.733; 1.5,1.5,3.5,3.5 => .339.
# SAS for first answer: mean(1,3/7)=5/7; exact three shared words: 3/5=.6.
CASES = {
    'full': (0.733, 0.735, 0.714, 1.0, 0.0, True, []),
    'json': (0.733, 0.801, 0.714, 1.0, 0.0, True, []),
    'threshold': (0.733, 0.735, 0.6, 0.6, 0.4, True, []),
    'partial': (1.0, 0.56, 0.714, 1.0, 0.0, True, []),
    'disagreement': (0.339, 0.735, 0.0, 0.0, 1.0, False,
                     ['low_consensus', 'hallucination_risk', 'synthesis_not_grounded']),
    'tied': (1.0, 0.735, 0.0, 0.0, 1.0, False,
             ['hallucination_risk', 'synthesis_not_grounded']),
    # Mixed: 4 replies, including two nonvoters, with mean length 7/50.
    'mixed': (1.0, 0.717, 0.714, 1.0, 0.0, True, []),
    # Shallow: identical token sets, two returned reviews, 10-word critiques.
    'shallow': (1.0, 0.235, 1.0, 1.0, 0.0, True, ['shallow_deliberation']),
    'unranked': None,
    'failed-review': None,
    'failed-synthesis': None,
    'disabled': None,
}


def expected_metrics(case):
    expected = CASES[case]
    if expected is None:
        return None
    css, ddi, winner, maximum, risk, grounded, alerts = expected
    result = {'tier': 'core', 'core': {'consensus_strength': css, 'deliberation_depth': ddi,
              'synthesis_attribution': {'winner_alignment': winner, 'max_source_alignment': maximum,
                                        'hallucination_risk': risk, 'grounded': grounded}}}
    if alerts:
        result['quality_alerts'] = alerts
    return result


def answers(case):
    if case == 'shallow':
        return dict(zip(MODELS, ('alpha beta', 'beta alpha', 'ALPHA beta', 'alpha BETA')))
    return ANSWERS


def synthesis(case):
    if case in ('disagreement', 'tied'):
        return 'unknown novel'
    if case == 'threshold':
        return 'alpha beta gamma'
    return answers(case)[CHAIRMAN]


def ballot(case, model, prompt):
    labels = {content: 'Response ' + label for label, content in
              re.findall(r'<candidate_response id="([A-Z])">\n(.*?)\n</candidate_response>', prompt, re.S)}
    assert set(labels) == set(answers(case).values())
    order = list(MODELS)
    i = MODELS.index(model)
    if case == 'disagreement' and i % 2:
        order = [MODELS[1], MODELS[0], MODELS[3], MODELS[2]]
    if case == 'tied':
        order = order[i:] + order[:i]
    if case in ('partial', 'mixed', 'shallow'):
        order = order[:2]
    if case == 'mixed' and model == MODELS[2]:
        return 'No usable ballot returned.'
    if case == 'mixed' and model == MODELS[3]:
        return 'I cannot evaluate candidates.'
    if case == 'unranked':
        order = order[:1]
    ranked = [labels[answers(case)[m]] for m in order]
    if case == 'json':
        return CRITIQUE + '\n```json\n' + json.dumps(
            {'ranking': ranked, 'scores': dict(zip(ranked, (10, 7, 4, 1)))}, separators=(',', ':')) + '\n```'
    return CRITIQUE + '\nFINAL RANKING:\n' + '\n'.join(f'{i}. {label}' for i, label in enumerate(ranked, 1))


def prepare(gateway_config, monkeypatch, case):
    cfg = yaml.safe_load(gateway_config.read_text(encoding='utf-8'))
    cfg['council']['exclude_self_votes'] = False
    gateway_config.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    monkeypatch.setenv('LLM_COUNCIL_QUALITY_METRICS', 'false' if case == 'disabled' else 'true')
    monkeypatch.setenv('LLM_COUNCIL_QUALITY_TIER', 'core')
    reload_config()
    calls = []

    async def transport(_transport, request):
        assert str(request.url) == PRIMARY
        assert request.headers['authorization'] == f'Bearer {KEYS[PRIMARY]}'
        body = json.loads(request.content)
        model, prompt = body['model'], body['messages'][-1]['content']
        assert QUESTION in prompt
        status = 200
        if prompt == QUESTION:
            stage, answer = 'stage1', answers(case)[model]
        elif '<responses_to_evaluate>' in prompt:
            stage, answer = 'stage2', ballot(case, model, prompt)
            if case in ('partial', 'unranked', 'shallow') and model in MODELS[2:]:
                status = 503
            if case == 'failed-review':
                status = 503
        elif prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        else:
            stage, answer = 'stage3', synthesis(case)
            assert model == CHAIRMAN
            assert prompt.startswith('You are the Chairman of an LLM Council.')
            if case == 'failed-synthesis':
                status = 503
        calls.append((stage, model, status))
        if status != 200:
            return httpx.Response(status, json={'error': {'message': 'Fixture failure'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0}})

    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
    return calls


def verify(case, calls, stage1, stage2, stage3, metadata, title):
    expected = [('stage1', m, 200) for m in MODELS]
    expected += [('stage2', m, 503 if case == 'failed-review' or
                  (case in ('partial', 'unranked', 'shallow') and m in MODELS[2:]) else 200) for m in MODELS]
    expected.append(('stage3', CHAIRMAN, 503 if case == 'failed-synthesis' else 200))
    if title:
        expected.append(('title', TITLE_MODEL, 200))
    assert Counter(calls) == Counter(expected)
    assert {r['model']: r['response'] for r in stage1} == answers(case)
    assert len(stage2) == (0 if case == 'failed-review' else 2 if case in ('partial', 'unranked', 'shallow') else 4)
    positions = {r['model']: r['average_position'] for r in metadata['aggregate_rankings']}
    if case == 'failed-review':
        assert all(value is None for value in positions.values())
    elif case == 'unranked':
        assert positions == dict(zip(MODELS, (1.0, None, None, None)))
    elif case in ('partial', 'mixed', 'shallow'):
        assert positions == dict(zip(MODELS, (1.0, 2.0, None, None)))
    elif case == 'disagreement':
        assert positions == dict(zip(MODELS, (1.5, 1.5, 3.5, 3.5)))
    elif case == 'tied':
        assert positions == dict.fromkeys(MODELS, 2.5)
    else:
        assert positions == dict(zip(MODELS, (1.0, 2.0, 3.0, 4.0)))
    if case == 'failed-synthesis':
        assert stage3.get('error_status')
    else:
        assert stage3['response'] == synthesis(case)
        assert not stage3.get('error_status')
    # Native raw review and its parsed ballot must remain unchanged by metric projection.
    labels = metadata['label_to_model']
    xml = '\n'.join(f'<candidate_response id="{label[-1]}">\n{answers(case)[entry["model"]]}\n</candidate_response>'
                    for label, entry in labels.items())
    for review in stage2:
        assert review['ranking'] == ballot(case, review['model'], xml)
        assert 'content' not in review
    assert metadata.get('quality_metrics') == expected_metrics(case)
    if case == 'disabled':
        assert 'quality_metrics' not in metadata
    review = metadata['peer_review']
    assert review['ranked_candidates'] == (0 if case == 'failed-review' else 1 if case == 'unranked'
                                          else 2 if case in ('partial', 'mixed', 'shallow') else 4)
    if case == 'mixed':
        assert review['invalid_reviews'] == 1
        assert review['abstained_reviews'] == 1
        assert review['partial_reviews'] == 2
    assert review['state'] == ('failed' if case == 'failed-review' else 'partial' if case in ('partial', 'mixed', 'unranked', 'shallow') else 'completed')
    assert metadata['usage']['total']['total_tokens'] == 18 * sum(
        status == 200 for stage, _, status in calls if stage != 'title')


@pytest.mark.parametrize('case', CASES)
def test_native_quality_projection(gateway_config, monkeypatch, case):
    from backend import engine_runtime
    calls = prepare(gateway_config, monkeypatch, case)
    stage1, stage2, stage3, metadata = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    metadata = engine_runtime.annotate_peer_review(stage1, stage2, stage3, metadata)
    original = deepcopy((stage1, stage2, stage3, metadata))
    corrected = engine_runtime.repair_quality_metrics(stage1, stage2, stage3, metadata)
    assert (stage1, stage2, stage3, metadata) == original
    verify(case, calls, stage1, stage2, stage3, corrected, False)


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_quality_api(gateway_config, monkeypatch, tmp_path, case, stream):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    calls = prepare(gateway_config, monkeypatch, case)
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        created = client.post('/api/conversations', json={})
        assert created.status_code == 200
        cid = created.json()['id']
        response = client.post(f'/api/conversations/{cid}/message' + ('/stream' if stream else ''),
                               json={'content': QUESTION})
        assert response.status_code == 200, response.text
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            terminal = {e['type']: e for e in events if e['type'] != 'engine_progress'}
            assert 'error' not in terminal
            result = {stage: terminal[f'{stage}_complete']['data'] for stage in ('stage1', 'stage2', 'stage3')}
            result['metadata'] = terminal['complete']['metadata']
            assert terminal['stage2_complete']['metadata'] == result['metadata']
            assert terminal['stage3_complete']['metadata'] == result['metadata']
            assert terminal['title_complete']['data']['title'] == TITLE
            progress = [e['data'] for e in events if e['type'] == 'engine_progress']
            assert result['metadata']['ui_runtime_progress'] == progress
            assert progress[-1]['state'] == ('failed' if case == 'failed-synthesis' else 'completed')
        else:
            result = response.json()
        verify(case, calls, result['stage1'], result['stage2'], result['stage3'], result['metadata'], True)
        disk = storage.get_conversation(cid)
        assert disk['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **result}]
        assert disk['title'] == TITLE
        assert client.get(f'/api/conversations/{cid}').json() == disk

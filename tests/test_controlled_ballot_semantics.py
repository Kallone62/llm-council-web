"""ENG-31: mentioning candidates is not a peer-review ballot."""
import asyncio
import json
from collections import Counter

import pytest
from llm_council import council

from test_controlled_gateway_fallback import CHAIRMAN, MODELS, TITLE_MODEL, gateway_config
from test_controlled_holistic_json import ANSWERS, FINAL, QUESTION, install_provider, run_ballot_api

# Fixed independent expected count of ranked candidates; no parser-derived oracle.
CASES = {
    'raw-json': 4, 'raw-scores-first': 4, 'raw-brace-string': 4, 'fenced-json': 4, 'legacy-numbered': 4, 'bare-numbered': 4,
    'legacy-labels': 4, 'legacy-inline': 4, 'bare-chain': 4, 'legacy-single': 1,
    'malformed-with-legacy': 4,
    'prose': 0, 'header-prose': 0, 'quoted-header': 0, 'numbered-critique': 0, 'headed-critique': 0,
    'malformed-raw': 0, 'malformed-fenced': 0, 'empty-heading': 0, 'label-prefix': 0,
}


def ballot_text(labels, case, _fenced, _rubric):
    ordered = [labels[model] for model in MODELS]
    a, b, c, d = ordered
    numbered = '\n'.join(f'{i}. {label}' for i, label in enumerate(ordered, 1))
    raw = json.dumps({'ranking': ordered, 'scores': {}})
    # These reviews deliberately do NOT express a ranking, even when every label appears.
    critique = f'{a} and {b} use different methods; {c} and {d} also need evidence. No order is assigned.'
    malformed = '{"ranking":' + json.dumps(ordered)[:-1] + ',}'
    return {
        'raw-json': raw,
        'raw-scores-first': json.dumps({'scores': {}, 'ranking': ordered}),
        'raw-brace-string': json.dumps({'ranking': ordered, 'notes': 'Literal } brace', 'scores': {}}),
        'fenced-json': f'{critique}\n```json\n{raw}\n```',
        'legacy-numbered': f'{critique}\nFINAL RANKING:\n{numbered}',
        'bare-numbered': numbered,
        'legacy-labels': 'FINAL RANKING:\n' + '\n'.join(ordered),
        'legacy-inline': 'FINAL RANKING: ' + ', '.join(ordered),
        'bare-chain': ' > '.join(ordered),
        'legacy-single': f'FINAL RANKING:\n1. {a}',
        'malformed-with-legacy': f'```json\n{malformed}\n```\nFINAL RANKING:\n{numbered}',
        'prose': critique,
        'header-prose': f'FINAL RANKING:\n{critique}',
        'quoted-header': 'Example only, not a ballot. FINAL RANKING: ' + ', '.join(ordered),
        'numbered-critique': '\n'.join(f'{i}. {label} is one candidate; no order assigned.' for i, label in enumerate(ordered, 1)),
        'headed-critique': 'FINAL RANKING:\n' + '\n'.join(f'{i}. {label} is one candidate; no order assigned.' for i, label in enumerate(ordered, 1)),
        'malformed-raw': malformed,
        'malformed-fenced': f'```json\n{malformed}\n```',
        'empty-heading': f'{critique}\nFINAL RANKING:',
        'label-prefix': 'FINAL RANKING:\n' + '\n'.join(label + 'BC' for label in ordered),
    }[case]


def verify_ballot(payload, observation, case, rubric, api):
    calls, labels, raw = observation
    count = CASES[case]
    expected_calls = [(stage, model) for stage in ('stage1', 'stage2') for model in MODELS]
    expected_calls += [('stage3', CHAIRMAN)] + ([('title', TITLE_MODEL)] if api else [])
    assert Counter(calls) == Counter(expected_calls)
    assert {r['model']: r['response'] for r in payload['stage1']} == ANSWERS
    assert len(payload['stage2']) == 4 and {r['model'] for r in payload['stage2']} == set(MODELS)
    metadata = payload['metadata']
    assert {entry['model']: label for label, entry in metadata['label_to_model'].items()} == labels
    expected_ranking = [labels[m] for m in MODELS[:count]]
    for review in payload['stage2']:
        assert review['ranking'] == raw[review['model']]
        parsed = review['parsed_ranking']
        assert parsed['ranking'] == expected_ranking
        assert parsed['scores'] == {} and not parsed.get('abstained')
        if rubric:
            assert parsed['rubric_scoring'] is False
        else:
            assert 'rubric_scoring' not in parsed
    rows = {r['model']: r for r in metadata['aggregate_rankings']}
    assert set(rows) == set(MODELS)
    for i, model in enumerate(MODELS):
        row = rows[model]
        assert row['vote_count'] == (3 if i < count else 0)
        assert row['average_position'] == (float(i+1) if i < count else None)
        assert row['borda_score'] == ((1.0, .667, .333, 0.0)[i] if i < count else None)
        assert row['average_score'] is None and row['self_votes_excluded'] is True
    if count == 4:
        assert [r['model'] for r in metadata['aggregate_rankings']] == list(MODELS)
    assert payload['stage3'] == {'model': CHAIRMAN, 'response': FINAL}
    assert metadata['usage']['total']['total_tokens'] == 162
    if api:
        state = 'completed' if count == 4 else 'partial' if count else 'failed'
        peer = metadata['peer_review']
        assert peer['state'] == state and peer['reviews'] == 4
        assert peer['effective_votes'] == 3*count and peer['ranked_candidates'] == count
        assert (peer['valid_reviews'], peer['partial_reviews'], peer['invalid_reviews'], peer['abstained_reviews']) == (
            (4, 0, 0, 0) if count == 4 else (0, 4, 0, 0) if count else (0, 0, 4, 0))
        if count == 4:
            assert metadata.get('status') not in ('failed', 'partial')
        else:
            assert metadata['status'] == 'partial'
    json.dumps(payload, allow_nan=False)


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('rubric', [False, True], ids=['holistic', 'rubric-fallback'])
def test_native_ballot_semantics(gateway_config, monkeypatch, case, rubric):
    observation = install_provider(gateway_config, monkeypatch, case, False, rubric, ballot_builder=ballot_text)
    result = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    verify_ballot(dict(zip(('stage1', 'stage2', 'stage3', 'metadata'), result)), observation, case, rubric, False)


@pytest.mark.parametrize('case', CASES)
@pytest.mark.parametrize('rubric', [False, True], ids=['holistic', 'rubric-fallback'])
@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
def test_ballot_semantics_api(gateway_config, monkeypatch, tmp_path, case, rubric, stream):
    observation = install_provider(gateway_config, monkeypatch, case, False, rubric, ballot_builder=ballot_text)
    expected_state = 'completed' if CASES[case] == 4 else 'partial' if CASES[case] else 'failed'
    payload = run_ballot_api(monkeypatch, tmp_path, stream, expected_review_state=expected_state)
    verify_ballot(payload, observation, case, rubric, True)

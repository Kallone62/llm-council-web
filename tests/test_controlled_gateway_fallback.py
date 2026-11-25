"""Acceptance for the pinned native gateway policy (not Free model fallback)."""
import asyncio
import json
import os
import socket
from collections import Counter

import httpx
import pytest
import yaml
from llm_council import council, gateway_adapter
from llm_council.unified_config import get_config, reload_config

MODELS = ('openai/gpt-5.6-sol', 'anthropic/claude-opus-5', 'deepseek/deepseek-v4-pro-0813', 'z-ai/glm-5.3')
CHAIRMAN = MODELS[0]
PRIMARY = 'https://openrouter.ai/api/v1/chat/completions'
SECONDARY = 'https://router.requesty.ai/v1/chat/completions'
KEYS = {PRIMARY: 'eng18-openrouter-test-key', SECONDARY: 'eng18-requesty-test-key'}
QUESTION = 'ENG18: Which archive should be retained?'
FINAL = 'Retain the verified archive.'
TITLE = 'Archive Retention'
TITLE_MODEL = 'google/gemini-2.5-flash'
LABELS = [f'Response {letter}' for letter in 'ABCD']
REVIEW = 'All candidates considered.\n```json\n' + json.dumps({'ranking': LABELS, 'scores': dict(zip(LABELS, (10, 7, 4, 1)))}) + '\n```'


@pytest.fixture
def gateway_config(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(('LLM_COUNCIL_', 'OTEL_', 'POSTHOG_')) or key.lower().endswith('_proxy'):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY', KEYS[PRIMARY])
    monkeypatch.setenv('REQUESTY_API_KEY', KEYS[SECONDARY])
    monkeypatch.setenv('LLM_COUNCIL_PERFORMANCE_TRACKING', 'false')
    monkeypatch.setenv('LLM_COUNCIL_EARLY_CONSENSUS', 'false')
    monkeypatch.setenv('LLM_COUNCIL_PERFORMANCE_STORE', str(tmp_path / 'performance.jsonl'))
    path = tmp_path / 'gateways.yaml'
    path.write_text(yaml.safe_dump({
        'tiers': {'default': 'high'},
        'council': {'models': list(MODELS), 'style_normalization': False, 'exclude_self_votes': True},
        'gateways': {'default': 'openrouter', 'providers': {
            'openrouter': {'enabled': True, 'base_url': PRIMARY},
            'requesty': {'enabled': True, 'base_url': SECONDARY},
        }, 'fallback': {'enabled': False, 'chain': ['openrouter', 'requesty'],
                        'retry_on': ['timeout', 'rate_limit', 'server_error'],
                        'do_not_retry_on': ['auth_error', 'invalid_request', 'content_filter']}},
        'cache': {'enabled': False},
        'evaluation': {'rubric': {'enabled': False}, 'safety': {'enabled': False},
                       'bias': {'audit_enabled': False, 'persistence_enabled': False}},
    }), encoding='utf-8')
    monkeypatch.setenv('LLM_COUNCIL_CONFIG', str(path))
    monkeypatch.setattr(gateway_adapter, '_gateway_router', None)
    # Only transport is mocked; no real provider traffic is allowed.
    def deny(*args, **kwargs):
        pytest.fail('Unexpected external network in gateway acceptance')
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    connect = socket.socket.connect
    def guarded_connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            deny()
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    reload_config()
    return path


def decode_openrouter_review_request(request, question):
    """Validate the real HTTP boundary and extract the model and user prompt."""
    assert (request.method, str(request.url), request.headers.get('authorization')) == (
        'POST', PRIMARY, f'Bearer {KEYS[PRIMARY]}')
    body = json.loads(request.content)
    model, prompt = body['model'], body['messages'][-1]['content']
    assert question in prompt
    return model, prompt


def set_policy(path, enabled):
    document = yaml.safe_load(path.read_text(encoding='utf-8'))
    document['gateways']['fallback']['enabled'] = enabled
    path.write_text(yaml.safe_dump(document), encoding='utf-8')
    reload_config()


def install_transport(monkeypatch, primary_status, secondary_status=200):
    calls = []
    async def handle(_transport, request):
        url = str(request.url)
        assert request.method == 'POST' and url in KEYS
        assert request.headers['authorization'] == f'Bearer {KEYS[url]}'
        body = json.loads(request.content)
        model, prompt = body['model'], body['messages'][-1]['content']
        assert QUESTION in prompt
        if prompt.startswith('Generate a very short title'):
            stage, answer = 'title', TITLE
            assert model == TITLE_MODEL
        elif prompt == QUESTION:
            stage, answer = 'stage1', f'{model}: preserve archive.'
            assert model in MODELS
        elif '<responses_to_evaluate>' in prompt:
            stage, answer = 'stage2', REVIEW
            assert model in MODELS
        else:
            stage, answer = 'stage3', FINAL
            assert model == CHAIRMAN and prompt.startswith('You are the Chairman of an LLM Council.')
        status = (primary_status if url == PRIMARY else secondary_status) if stage == 'stage3' else 200
        calls.append((stage, model, url, status))
        if status != 200:
            return httpx.Response(status, json={'error': {'message': f'Fixture HTTP {status}'}}, headers={'Retry-After': '1'})
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': answer}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0125}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle)
    return calls


CASES = [(False, 503, False), (True, 503, True), (True, 429, True), (True, 401, False)]
CASE_IDS = ['off-server-error', 'on-server-error', 'on-rate-limit', 'on-auth-error']


def assert_gateway_result(calls, stage1, stage2, stage3, metadata, code, recovery, title):
    expected = [('stage3', CHAIRMAN, PRIMARY, code)]
    if recovery:
        expected.append(('stage3', CHAIRMAN, SECONDARY, 200))
    assert [c for c in calls if c[0] == 'stage3'] == expected, f'Native gateway chain not executed: {calls}'
    other_calls = [(stage, model, PRIMARY, 200) for stage in ('stage1', 'stage2') for model in MODELS]
    if title:
        other_calls.append(('title', TITLE_MODEL, PRIMARY, 200))
    assert Counter(c for c in calls if c[0] != 'stage3') == Counter(other_calls)
    assert len(stage1) == len(stage2) == 4
    assert {r['model'] for r in stage1} == {r['model'] for r in stage2} == set(MODELS)
    assert len(metadata['aggregate_rankings']) == 4
    if recovery:
        assert stage3['response'] == FINAL and not stage3.get('error_status')
        assert metadata.get('status') not in ('failed', 'partial')
        assert metadata['usage']['total']['total_tokens'] == 162
    else:
        assert stage3.get('error_status') and stage3['response'] != FINAL
        assert metadata['usage']['total']['total_tokens'] == 144


@pytest.mark.parametrize('enabled,code,recovery', CASES, ids=CASE_IDS)
def test_native_compatibility_gateway_policy(gateway_config, monkeypatch, enabled, code, recovery):
    set_policy(gateway_config, enabled)
    assert get_config().gateways.fallback.enabled is enabled
    assert get_config().get_fallback_chain() == (['openrouter', 'requesty'] if enabled else ['openrouter'])
    calls = install_transport(monkeypatch, code)
    result = asyncio.run(council.run_full_council(QUESTION, bypass_cache=True))
    assert_gateway_result(calls, *result, code, recovery, False)


@pytest.mark.parametrize('code,recovery', [(503, True), (401, False)], ids=['retry-server-error', 'stop-auth-error'])
def test_native_router_explicit_chain_respects_error_policy(gateway_config, monkeypatch, code, recovery):
    """Separately exercise the native L4 router, with explicit real gateways.

    No adapter flags or fake routers are installed into the compatibility path.
    """
    from llm_council.gateway.openrouter import OpenRouterGateway
    from llm_council.gateway.requesty import RequestyGateway
    from llm_council.gateway.router import GatewayRouter
    from llm_council.gateway.types import CanonicalMessage, ContentBlock, GatewayRequest
    set_policy(gateway_config, True)
    calls = []
    async def transport(_transport, request):
        url = str(request.url)
        assert url in KEYS and request.headers['authorization'] == f'Bearer {KEYS[url]}'
        assert json.loads(request.content)['model'] == CHAIRMAN
        calls.append(url)
        if url == PRIMARY:
            return httpx.Response(code, json={'error': {'message': f'Fixture {code}'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': FINAL}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18, 'cost': 0.0125}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', transport)
    router = GatewayRouter(gateways={'openrouter': OpenRouterGateway(api_key=KEYS[PRIMARY]),
                                     'requesty': RequestyGateway(api_key=KEYS[SECONDARY])},
                           fallback_chains={'openrouter': ['requesty']})
    request = GatewayRequest(model=CHAIRMAN, messages=[CanonicalMessage(role='user', content=[ContentBlock(type='text', text=QUESTION)])])
    # Capture the native failure without replacing any provider or router method.
    response = None
    try:
        response = asyncio.run(router.complete(request))
    except Exception:
        if recovery:
            raise
    assert calls == ([PRIMARY, SECONDARY] if recovery else [PRIMARY]), f'Unexpected failover on HTTP {code}: {calls}'
    if recovery:
        assert response.status == 'ok' and response.content == FINAL
    else:
        assert response is None or response.status != 'ok'


@pytest.mark.parametrize('stream', [False, True], ids=['rest', 'sse'])
@pytest.mark.parametrize('enabled,code,recovery', CASES, ids=CASE_IDS)
def test_api_gateway_policy(gateway_config, monkeypatch, tmp_path, stream, enabled, code, recovery):
    from fastapi.testclient import TestClient
    from backend import config, main, storage
    monkeypatch.setattr(config, 'ENV_PATH', tmp_path / '.env')
    monkeypatch.setattr(storage, 'DATA_DIR', str(tmp_path / 'conversations'))
    reload_config()
    calls = install_transport(monkeypatch, code)
    assert main.run_full_council is council.run_full_council
    with TestClient(main.app) as client:
        # Free→High is the existing settings API transition that restores
        # fallback.enabled. A normal High save preserves a disabled YAML policy.
        if enabled:
            free = client.put('/api/settings', json={'profile': 'free'})
            assert free.status_code == 200 and get_config().gateways.fallback.enabled is False
        selected = client.put('/api/settings', json={'profile': 'high'})
        assert selected.status_code == 200 and selected.json()['profile'] == 'high'
        assert yaml.safe_load(gateway_config.read_text(encoding='utf-8'))['gateways']['fallback']['enabled'] is enabled
        reload_config()
        assert get_config().gateways.fallback.enabled is enabled and not calls
        assert client.get('/api/settings').json()['profile'] == 'high'
        created = client.post('/api/conversations', json={})
        assert created.status_code == 200
        cid = created.json()['id']
        url = f'/api/conversations/{cid}/message' + ('/stream' if stream else '')
        response = client.post(url, json={'content': QUESTION})
        assert response.status_code == 200
        if stream:
            events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
            terminal = {e['type']: e for e in events if e['type'] != 'engine_progress'}
            result = {stage: terminal[f'{stage}_complete']['data'] for stage in ('stage1', 'stage2', 'stage3')}
            result['metadata'] = terminal['complete']['metadata']
            assert terminal['stage3_complete']['metadata'] == result['metadata']
            assert terminal['title_complete']['data']['title'] == TITLE
            progress = [e['data'] for e in events if e['type'] == 'engine_progress']
            assert result['metadata']['ui_runtime_progress'] == progress
            assert progress[-1]['stage'] == 'stage3' and progress[-1]['state'] == ('completed' if recovery else 'failed')
        else:
            result = response.json()
        assert_gateway_result(calls, result['stage1'], result['stage2'], result['stage3'], result['metadata'], code, recovery, True)
        assert result['metadata']['peer_review']['effective_votes'] == 12
        assert result['metadata'].get('status') != 'failed' if recovery else result['metadata']['status'] == 'failed'
        disk = storage.get_conversation(cid)
        assert disk['messages'] == [{'role': 'user', 'content': QUESTION}, {'role': 'assistant', **result}]
        assert disk['title'] == TITLE and client.get(f'/api/conversations/{cid}').json() == disk

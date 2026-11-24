"""Additional native gateway policy tests; leave original RED acceptance untouched."""
import asyncio
import json
import time

import httpx
import pytest
import yaml
from llm_council import openrouter
from llm_council.gateway.types import ReasoningParams
from llm_council.unified_config import reload_config
from test_controlled_gateway_fallback import (gateway_config, PRIMARY, SECONDARY, KEYS, CHAIRMAN, QUESTION, FINAL)


@pytest.mark.parametrize('code,config_change,expected', [
    (503, 'normal', [PRIMARY, SECONDARY]),
    (429, 'normal', [PRIMARY, SECONDARY]),
    (400, 'normal', [PRIMARY]),
    (401, 'normal', [PRIMARY]),
    (403, 'normal', [PRIMARY]),
    (404, 'normal', [PRIMARY]),
    (503, 'missing-key', [PRIMARY]),
    (503, 'disabled-provider', [PRIMARY]),
    (503, 'missing-provider', [PRIMARY]),
    (503, 'retry-excluded', [PRIMARY]),
    (503, 'veto', [PRIMARY]),
    (503, 'off', [PRIMARY]),
    (503, 'free', [PRIMARY]),
    (503, 'duplicate-chain', [PRIMARY, SECONDARY]),
    (503, 'exhausted', [PRIMARY, SECONDARY]),
    (503, 'remap', [PRIMARY, SECONDARY]),
], ids=['503','429','400','401','403','404','missing-key','disabled-provider','missing-provider',
        'retry-excluded','veto','off','free','dedup','exhausted','remap'])
def test_native_transport_policy(gateway_config, monkeypatch, code, config_change, expected):
    cfg = yaml.safe_load(gateway_config.read_text())
    policy = cfg['gateways']['fallback']
    policy['enabled'] = config_change != 'off'
    providers = cfg['gateways']['providers']
    model = CHAIRMAN
    if config_change == 'missing-key':
        monkeypatch.delenv('REQUESTY_API_KEY')
    elif config_change == 'disabled-provider':
        providers['requesty']['enabled'] = False
    elif config_change == 'missing-provider':
        del providers['requesty']
    elif config_change == 'retry-excluded':
        policy['retry_on'] = ['timeout', 'rate_limit']
    elif config_change == 'veto':
        policy['do_not_retry_on'].append('server_error')
    elif config_change == 'free':
        model += ':free'
    elif config_change == 'duplicate-chain':
        policy['chain'] = ['openrouter','openrouter','direct','requesty','requesty']
    elif config_change == 'remap':
        cfg['gateways']['model_name_map'] = {'requesty': {model: 'mapped-model'}}
    gateway_config.write_text(yaml.safe_dump(cfg))
    reload_config()
    calls = []
    async def handle(_transport, request):
        url = str(request.url)
        calls.append(url)
        assert request.headers['authorization'] == 'Bearer ' + KEYS[url]
        body = json.loads(request.content)
        assert body['model'] == ('mapped-model' if url == SECONDARY and config_change == 'remap' else model)
        assert body['messages'] == [{'role':'user','content':QUESTION}]
        assert body['tools'] == [] and body['tool_choice'] == 'none'
        assert body['reasoning']['effort'] == 'high'
        if url == PRIMARY or config_change == 'exhausted':
            return httpx.Response(code, json={'error': {'message': 'fixture'}})
        return httpx.Response(200, json={'choices':[{'message':{'content':FINAL,'reasoning_details':[{'text':'fixture reasoning'}]}}],
            'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18,'cost':0.0125,'cached_tokens':3}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',handle)
    result = asyncio.run(openrouter.query_model_with_status(model,[{'role':'user','content':QUESTION}],
        timeout=2,disable_tools=True,reasoning_params=ReasoningParams(effort='high',max_tokens=1024)))
    assert calls == expected
    if len(expected) == 2 and config_change != 'exhausted':
        assert result['status'] == 'ok' and result['content'] == FINAL and result['route'] == 'requesty'
        assert result['reasoning_details'] == [{'text':'fixture reasoning'}]
        assert result['usage']['total_tokens'] == 18 and result['usage']['cost'] == 0.0125
        assert result['usage']['cached_tokens'] == 3
    else:
        assert result['status'] != 'ok'


@pytest.mark.parametrize('kind', ['early-timeout','exhaust-budget','second-timeout','cancel'])
def test_native_timeout_budget(gateway_config, monkeypatch, kind):
    cfg = yaml.safe_load(gateway_config.read_text())
    cfg['gateways']['fallback']['enabled'] = True
    gateway_config.write_text(yaml.safe_dump(cfg)); reload_config()
    calls = []
    async def handle(_transport, request):
        url = str(request.url)
        calls.append((url, request.extensions['timeout']['read']))
        if url == PRIMARY:
            if kind == 'early-timeout':
                await asyncio.sleep(.015)
                raise httpx.ReadTimeout('fixture',request=request)
            if kind in ('exhaust-budget','cancel'):
                await asyncio.sleep(1)
            await asyncio.sleep(.02)
            return httpx.Response(503)
        if kind == 'second-timeout':
            await asyncio.sleep(1)
        return httpx.Response(200,json={'choices':[{'message':{'content':FINAL}}],'usage':{'total_tokens':18}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',handle)
    async def run():
        task=asyncio.create_task(openrouter.query_model_with_status(CHAIRMAN,[{'role':'user','content':QUESTION}],timeout=.12))
        if kind == 'cancel':
            await asyncio.sleep(.02); task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return None
        return await task
    start=time.monotonic(); result=asyncio.run(run()); elapsed=time.monotonic()-start
    assert elapsed < .6
    if kind in ('exhaust-budget','cancel'):
        assert [url for url,_ in calls] == [PRIMARY]
    else:
        assert [url for url,_ in calls] == [PRIMARY,SECONDARY]
        assert 0 < calls[1][1] < calls[0][1] == .12
    if kind == 'early-timeout':
        assert result['status']=='ok' and result['route']=='requesty'
    elif kind != 'cancel':
        assert result['status']=='timeout'


@pytest.mark.parametrize('code,error,expected', [
    (503, {'code':'content_filter'}, [PRIMARY]),
    (503, {'type':'content_filter'}, [PRIMARY]),
    (429, {'code':'content_filter'}, [PRIMARY]),
])
def test_explicit_content_filter_is_terminal(gateway_config, monkeypatch, code, error, expected):
    cfg = yaml.safe_load(gateway_config.read_text())
    cfg['gateways']['fallback']['enabled'] = True
    gateway_config.write_text(yaml.safe_dump(cfg)); reload_config()
    calls = []
    async def handle(_transport, request):
        url = str(request.url); calls.append(url)
        return httpx.Response(code, json={'error':error})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',handle)
    result = asyncio.run(openrouter.query_model_with_status(CHAIRMAN,[{'role':'user','content':QUESTION}]))
    assert calls == expected and result['status'] != 'ok'


@pytest.mark.parametrize('code,allowed,veto,expected', [
    (503, True, False, [PRIMARY, SECONDARY]), (429, True, False, [PRIMARY, SECONDARY]),
    (400, True, False, [PRIMARY]), (401, True, False, [PRIMARY]), (403, True, False, [PRIMARY]),
    (503, False, False, [PRIMARY]), (503, True, True, [PRIMARY]),
], ids=['503','429','400','401','403','off','veto'])
def test_l4_policy_and_persistent_router(gateway_config, monkeypatch, code, allowed, veto, expected):
    from llm_council.gateway.openrouter import OpenRouterGateway
    from llm_council.gateway.requesty import RequestyGateway
    from llm_council.gateway.router import GatewayRouter
    from llm_council.gateway.types import GatewayRequest, CanonicalMessage, ContentBlock
    cfg=yaml.safe_load(gateway_config.read_text()); policy=cfg['gateways']['fallback']
    policy['enabled']=allowed
    if veto:
        policy['do_not_retry_on'].append('server_error')
    gateway_config.write_text(yaml.safe_dump(cfg)); reload_config()
    calls=[]
    async def handle(_transport, request):
        url=str(request.url); calls.append(url)
        assert request.headers['authorization']=='Bearer '+KEYS[url]
        if url==PRIMARY:
            return httpx.Response(code,json={'error':{'message':'fixture'}})
        return httpx.Response(200,json={'choices':[{'message':{'content':FINAL}}],'usage':{'total_tokens':18}})
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',handle)
    router=GatewayRouter(gateways={'openrouter':OpenRouterGateway(api_key=KEYS[PRIMARY]),'requesty':RequestyGateway(api_key=KEYS[SECONDARY])},fallback_chains={'openrouter':['requesty']},circuit_breaker_config={'failure_threshold':2,'success_threshold':1,'timeout_seconds':60})
    request=GatewayRequest(model=CHAIRMAN,messages=[CanonicalMessage(role='user',content=[ContentBlock(type='text',text=QUESTION)])],timeout=1)
    async def run():
        for _ in range(2):
            result=await router.complete(request)
            assert (result.status=='ok')==(len(expected)==2)
        if len(expected)==2:
            assert router.get_stats()['gateways']['openrouter']['circuit_breaker']['state']=='open'
            result=await router.complete(request)
            assert result.status=='ok'
    asyncio.run(run())
    assert calls==expected*2+([SECONDARY] if len(expected)==2 else [])


@pytest.mark.parametrize('kind', ['filtered-429','filtered-503','exhausted','timeout','unknown'])
def test_l4_terminal_errors(gateway_config, monkeypatch, kind):
    from llm_council.gateway.openrouter import OpenRouterGateway
    from llm_council.gateway.requesty import RequestyGateway
    from llm_council.gateway.router import GatewayRouter
    from llm_council.gateway.types import GatewayRequest, CanonicalMessage, ContentBlock
    cfg=yaml.safe_load(gateway_config.read_text()); cfg['gateways']['fallback']['enabled']=True
    gateway_config.write_text(yaml.safe_dump(cfg)); reload_config()
    calls=[]
    async def handle(_transport, request):
        url=str(request.url); calls.append(url)
        if kind.startswith('filtered'):
            return httpx.Response(int(kind.split('-')[1]),json={'error':{'code':'content_filter'}})
        if kind=='timeout':
            await asyncio.sleep(1)
        if kind=='unknown':
            raise RuntimeError('unclassified fixture')
        return httpx.Response(503)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',handle)
    router=GatewayRouter(gateways={'openrouter':OpenRouterGateway(api_key=KEYS[PRIMARY]),'requesty':RequestyGateway(api_key=KEYS[SECONDARY])},fallback_chains={'openrouter':['requesty']})
    request=GatewayRequest(model=CHAIRMAN,messages=[CanonicalMessage(role='user',content=[ContentBlock(type='text',text=QUESTION)])],timeout=.12)
    if kind in ('exhausted','timeout'):
        with pytest.raises(Exception):
            asyncio.run(router.complete(request))
    else:
        result=asyncio.run(router.complete(request)); assert result.status != 'ok'
    assert calls==([PRIMARY,SECONDARY] if kind=='exhausted' else [PRIMARY])

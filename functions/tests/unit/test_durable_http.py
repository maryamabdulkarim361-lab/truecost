"""No token issuance, certificate fetch, ADC or provider calls."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from flask import Request
from werkzeug.test import EnvironBuilder
from services.durable_http import verify_service, durable_selected, handle, route_start
from services.cloud_tasks_dispatcher import CloudTasksDispatcher, DispatchUnavailable
from services.request_auth import AccessError
from tests.unit.test_durable_dispatch import config, payload

pytestmark = pytest.mark.usefixtures('block_network')


def request(body=None, method='POST', header='Bearer offline-token'):
    return Request(EnvironBuilder(method=method, json=body or {}, headers={'Authorization':header}).get_environ())


def claims():
    return {'iss':'https://accounts.google.com','aud':'https://worker.example.com',
            'email':'worker@example.iam.gserviceaccount.com','email_verified':True,'sub':'123'}


def test_identity_verified():
    c = claims()
    assert verify_service(request(), c['email'], c['aud'], lambda *a:c).principal == c['email']


@pytest.mark.parametrize('field,value', [('iss','https://securetoken.google.com/project'),('aud','wrong'),
    ('email','other@example.iam.gserviceaccount.com'),('email_verified',False),('sub','')])
def test_identity_rejected(field,value):
    c=claims(); c[field]=value
    with pytest.raises(AccessError):
        verify_service(request(),claims()['email'],claims()['aud'],lambda *a:c)


@pytest.mark.parametrize('header', ['', 'Basic fake','Bearer'])
def test_missing_auth_no_verification(header):
    with pytest.raises(AccessError):
        verify_service(request(header=header),'sa','aud',lambda *a:pytest.fail('verification called'))


def test_production_no_synchronous_fallback(monkeypatch):
    monkeypatch.setenv('APP_ENV','production'); monkeypatch.delenv('PIPELINE_EXECUTION_MODE',raising=False)
    with pytest.raises(ValueError): durable_selected()
    assert route_start(lambda r:pytest.fail('legacy invoked'))(request()).status_code == 503
    monkeypatch.setenv('PIPELINE_EXECUTION_MODE','durable'); assert durable_selected()


class Session:
    def __init__(self, status=200): self.status=status; self.calls=[]; self.closed=False
    def __enter__(self): return self
    def __exit__(self,*args): self.closed=True
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs)); return SimpleNamespace(status_code=self.status)


@pytest.mark.parametrize('status',[200,201,409,429,503])
@pytest.mark.asyncio
async def test_tasks_rest_protocol(status):
    session=Session(status); adapter=CloudTasksDispatcher(config(),lambda:session)
    if status in (429,503):
        with pytest.raises(DispatchUnavailable, match='Dispatch unavailable'):
            await adapter.publish(task_id='job-r0-p0',payload=payload(),not_before=1000)
    else: await adapter.publish(task_id='job-r0-p0',payload=payload(),not_before=1000)
    assert len(session.calls)==1 and session.closed
    url,kw=session.calls[0]
    assert url=='https://cloudtasks.googleapis.com/v2/'+config().queue_path+'/tasks'
    assert kw['timeout']==5 and kw['allow_redirects'] is False
    task=kw['json']['task']; assert task['dispatchDeadline']=='390s'
    import base64
    assert json.loads(base64.b64decode(task['httpRequest']['body']))==payload()
    assert task['httpRequest']['oidcToken']['audience']==config().worker_url


@pytest.mark.asyncio
async def test_invalid_task_never_acquires_credentials():
    adapter=CloudTasksDispatcher(config(),lambda:pytest.fail('credentials acquired'))
    with pytest.raises(DispatchUnavailable):
        await adapter.publish(task_id='wrong',payload=payload(),not_before=0)


@pytest.mark.asyncio
async def test_start_returns_202_no_agent(monkeypatch):
    from pathlib import Path
    fixture=json.loads(Path('functions/tests/fixtures/clarification_output_kitchen.json').read_text())
    monkeypatch.setattr('services.durable_boundaries.request_auth.authenticate', lambda r:'owner')
    core=SimpleNamespace(repo=SimpleNamespace(backend=SimpleNamespace(read=AsyncMock(return_value=SimpleNamespace(data={'ownerId':'owner'})))),
        start=AsyncMock(return_value={'httpStatus':202,'estimateId':'est','jobId':'job'}))
    body,status=await handle('start',request({'projectId':'project','idempotencyKey':'key','clarificationOutput':fixture}),core,
        execute=lambda *a:pytest.fail('inline agent'))
    assert status==202 and body['data']['status']=='accepted'
    core.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_clarification(monkeypatch):
    body,status=await handle('start',request({'clarificationOutput':{}}),SimpleNamespace())
    assert status==400 and 'Invalid clarification schema' in str(body)

@pytest.mark.asyncio
async def test_pricing_token_interface():
    from services.service_identity import service_headers
    seen=[]
    def acquire(audience): seen.append(audience); return 'offline-test-token'
    result=await service_headers('https://pricing.example.com/comparePricesService',acquire)
    assert seen==['https://pricing.example.com/comparePricesService']
    assert result['Authorization']=='Bearer offline-test-token'

@pytest.mark.asyncio
async def test_pricing_identity_failure_sanitized():
    from services.service_identity import service_headers
    def fail(audience): raise RuntimeError('sensitive-do-not-retain')
    with pytest.raises(RuntimeError) as error:
        await service_headers('https://pricing.example.com',fail)
    assert str(error.value)=='Service identity unavailable'

@pytest.mark.asyncio
async def test_start_spoof_and_auth_failures(monkeypatch):
    from pathlib import Path
    fixture=json.loads(Path('functions/tests/fixtures/clarification_output_kitchen.json').read_text())
    core=SimpleNamespace(repo=SimpleNamespace(backend=SimpleNamespace(read=AsyncMock(return_value=SimpleNamespace(data={'ownerId':'owner'})))),start=AsyncMock())
    body={'projectId':'project','idempotencyKey':'key','clarificationOutput':fixture,'userId':'spoof'}
    monkeypatch.setattr('services.durable_boundaries.request_auth.authenticate',lambda r:'owner')
    _,status=await handle('start',request(body),core)
    assert status==403;core.start.assert_not_called()
    def denied(r): raise AccessError(401,'Invalid authentication')
    monkeypatch.setattr('services.durable_boundaries.request_auth.authenticate',denied)
    _,status=await handle('start',request(body),core)
    assert status==401;core.start.assert_not_called()


def test_readiness_missing_configuration_fails_closed(monkeypatch):
    from services.readiness import check
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('PIPELINE_EXECUTION_MODE','synchronous')
    monkeypatch.setenv('LLM_PROVIDER','invalid')
    result=check(SimpleNamespace())
    assert not result['ready'] and not result['checks']['configuration']
    assert set(result)=={'ready','checks'}
    assert all(type(v) is bool for v in result['checks'].values())

@pytest.mark.parametrize('raises',[False,True])
@pytest.mark.asyncio
async def test_runtime_owns_firestore_loop(monkeypatch,raises):
    import asyncio
    from services.durable_http import runtime
    monkeypatch.setenv('APP_ENV','development');monkeypatch.setenv('K_SERVICE','')
    monkeypatch.setenv('PIPELINE_EXECUTION_MODE','durable')
    monkeypatch.setenv('DURABLE_QUEUE_PATH',config().queue_path)
    monkeypatch.setenv('DURABLE_DISPATCH_SERVICE_ACCOUNT',config().service_account)
    for kind,url in [('WORKER',config().worker_url),('SCANNER','https://scanner.example.com')]:
        monkeypatch.setenv(f'DURABLE_{kind}_URL',url)
        monkeypatch.setenv(f'DURABLE_{kind}_INVOKER',config().service_account)
    monkeypatch.setattr('firebase_admin.get_app',lambda:SimpleNamespace(project_id='offline-project',credential=SimpleNamespace(get_credential=lambda:object())))
    loops=[]
    async def close(): loops.append(asyncio.get_running_loop())
    transport=SimpleNamespace(close=AsyncMock(side_effect=close))
    monkeypatch.setattr('google.cloud.firestore_v1.async_client.AsyncClient',lambda **kw:SimpleNamespace(_firestore_api=SimpleNamespace(transport=transport)))
    owner=asyncio.get_running_loop()
    try:
        async with runtime():
            if raises: raise RuntimeError('fixture')
    except RuntimeError:
        assert raises
    assert loops==[owner]
    transport.close.assert_awaited_once()

@pytest.mark.asyncio
async def test_malformed_private_json_rejected():
    broken=Request(EnvironBuilder(method='POST',data='{',content_type='application/json').get_environ())
    body,status=await handle('worker',broken,SimpleNamespace())
    assert status==400 and body['error']['code']=='INVALID_REQUEST'

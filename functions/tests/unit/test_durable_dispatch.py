"""Payload builders only. No Cloud Tasks SDK, HTTP or token acquisition."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from services.durable_dispatch import TaskConfiguration, ScannerInvocation, DurableWorkerService
from services.durable_execution import Rejected, VerifiedService

pytestmark = pytest.mark.usefixtures('block_network')


def config():
    return TaskConfiguration('projects/offline-project/locations/us-central1/queues/durable',
        'https://us-central1-offline-project.cloudfunctions.net/durableWorker',
        'dispatch@offline-project.iam.gserviceaccount.com')


def payload():
    return {'jobId':'job','revision':0,'operationId':'job-r0','generation':0}


def test_payload_is_deterministic_identifier_only_and_oidc():
    cfg = config()
    a = cfg.build(task_id='job-r0-p0',payload=payload(),not_before=1000)
    assert a == cfg.build(task_id='job-r0-p0',payload=payload(),not_before=1000)
    request = a['task']['http_request']
    assert json.loads(request['body']) == payload()
    assert request['oidc_token']['audience'] == cfg.worker_url
    assert request['oidc_token']['service_account_email'] == cfg.service_account
    assert set(request['headers']) == {'Content-Type'}


@pytest.mark.parametrize('field', ['apiKey','userToken','output','ownerUid','authorization'])
def test_payload_extra_fields_rejected(field):
    with pytest.raises(Rejected):
        config().build(task_id='job-r0-p0',payload={**payload(),field:'fake-secret'},not_before=0)


@pytest.mark.parametrize('url', ['http://localhost:5003/worker','https://127.0.0.1/worker',
    'https://name:fake@example.com/worker','https://example.com/worker?key=fake'])
def test_worker_url_security(url):
    with pytest.raises(Rejected):
        TaskConfiguration(config().queue_path,url,config().service_account)


def test_missing_configuration_fails_closed(monkeypatch):
    for name in ('DURABLE_QUEUE_PATH','DURABLE_WORKER_URL','DURABLE_DISPATCH_SERVICE_ACCOUNT'):
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(Rejected):
        TaskConfiguration.from_environment()


@pytest.mark.parametrize('identity', [None, VerifiedService('wrong','scanner'),VerifiedService('sa','wrong')])
@pytest.mark.asyncio
async def test_scanner_identity(identity):
    scanner = SimpleNamespace(run_page=AsyncMock())
    with pytest.raises(Rejected):
        await ScannerInvocation(scanner,principal='sa',audience='scanner').handle(identity,{})
    scanner.run_page.assert_not_called()


@pytest.mark.asyncio
async def test_scanner_pagination_forwarded():
    scanner = SimpleNamespace(run_page=AsyncMock(return_value={'cursor':'next'}))
    service = ScannerInvocation(scanner,principal='sa',audience='scanner')
    assert await service.handle(VerifiedService('sa','scanner'),{'cursor':'job','limit':10}) == {'cursor':'next'}
    scanner.run_page.assert_awaited_once_with(after='job',limit=10)


@pytest.mark.parametrize('state', [None, {'status':'final'}, {'generation':2}, {'revision':2}, {'operationId':'wrong'}])
@pytest.mark.asyncio
async def test_worker_rejects_unknown_and_obsolete(state):
    job = {'status':'queued','revision':0,'generation':0,'dispatchIntent':{'operationId':'job-r0'}}
    if state is None:
        job = None
    elif 'operationId' in state:
        job['dispatchIntent'] = state
    else:
        job.update(state)
    execute = AsyncMock()
    core = SimpleNamespace(repo=SimpleNamespace(get=AsyncMock(return_value=job)))
    service = DurableWorkerService(core,principal='sa',audience='worker',execute=execute)
    assert await service.handle(VerifiedService('sa','worker'),payload(),lease_owner='w') == {'status':'rejected'}
    execute.assert_not_called()


@pytest.mark.parametrize('body', [{}, {'jobId':'job'}, {**payload(),'generation':True}])
@pytest.mark.asyncio
async def test_worker_invalid_envelope(body):
    service = DurableWorkerService(SimpleNamespace(),principal='sa',audience='worker',execute=AsyncMock())
    with pytest.raises(Rejected):
        await service.handle(VerifiedService('sa','worker'),body,lease_owner='w')

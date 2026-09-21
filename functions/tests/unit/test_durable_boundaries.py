"""Offline boundary security; no tokens verified over network and no agents."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from flask import Request
from werkzeug.test import EnvironBuilder
import pytest
from services.durable_boundaries import DurableStartService, DurableOutputBoundary
from services import request_auth
from services.durable_execution import VerifiedService, Rejected
from services.durable_store import Snapshot

pytestmark = pytest.mark.usefixtures('block_network')


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(request_auth.auth, 'verify_id_token', lambda token: {'uid':'owner'})
    backend = SimpleNamespace(read=AsyncMock(return_value=Snapshot({'ownerId':'owner'}, 1)))
    core = SimpleNamespace(repo=SimpleNamespace(backend=backend),
        start=AsyncMock(return_value={'httpStatus':202,'estimateId':'e','jobId':'j'}))
    return DurableStartService(core)


def req(body, header='Bearer fake', method='POST'):
    return Request(EnvironBuilder(method=method, json=body, headers={'Authorization':header}).get_environ())


@pytest.mark.parametrize('header', ['', 'Basic fake', 'Bearer'])
@pytest.mark.asyncio
async def test_missing_auth_cannot_read_or_start(service, header):
    with pytest.raises(request_auth.AccessError) as error:
        await service.handle(req({}, header))
    assert error.value.status == 401
    service.core.repo.backend.read.assert_not_called()
    service.core.start.assert_not_called()


@pytest.mark.parametrize('body', [{}, [], {'projectId':'../other'},
    {'projectId':'p','idempotencyKey':'key','clarificationOutput':None}])
@pytest.mark.asyncio
async def test_invalid_start_body(service, body):
    with pytest.raises(request_auth.AccessError) as error:
        await service.handle(req(body))
    assert error.value.status == 400
    service.core.start.assert_not_called()


@pytest.mark.asyncio
async def test_claimed_uid_cannot_override_verified_uid(service):
    with pytest.raises(request_auth.AccessError) as error:
        await service.handle(req({'projectId':'p','idempotencyKey':'key',
            'clarificationOutput':{},'userId':'attacker'}))
    assert error.value.status == 403
    service.core.start.assert_not_called()


@pytest.mark.asyncio
async def test_success_returns_accepted_without_worker(service):
    result = await service.handle(req({'projectId':'p','idempotencyKey':'key','clarificationOutput':{}}))
    assert result == {'httpStatus':202,'estimateId':'e','jobId':'j','status':'accepted'}
    authorized = service.core.start.call_args.args[0]
    assert authorized.owner_uid == 'owner' and authorized.project_id == 'p'


@pytest.mark.parametrize('identity', [None, VerifiedService('wrong','worker'), VerifiedService('sa','wrong')])
@pytest.mark.asyncio
async def test_output_boundary_requires_verified_identity(identity):
    core = SimpleNamespace(complete=AsyncMock())
    boundary = DurableOutputBoundary(core, allowed_principal='sa', audience='worker')
    with pytest.raises(Rejected):
        await boundary.commit(identity, None, {})
    core.complete.assert_not_called()

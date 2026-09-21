"""Real LOCAL Firestore CAS commits, fake dispatch and operations. No agents.

Opt-in: FIRESTORE_EMULATOR_HOST=127.0.0.1:8081; otherwise refuse setup.
Each test owns a random demo project and recursively removes only its documents.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import grpc
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import AsyncClient
from werkzeug.test import EnvironBuilder
from flask import Request
from config.errors import StructuredError, ErrorCode
from services.durable_store import FirestoreAtomicBackend, JobRepository, Write, Conflict
from services.durable_execution import DurableCore, AuthorizedStart, Envelope, VerifiedService, Worker, Rejected, AGENT_SEQUENCE
from services.durable_boundaries import IntentScanner, DurableStartService, DurableOutputBoundary, DurableWriteToken
from services import request_auth

HOST = '127.0.0.1:8081'


class FakeDispatcher:
    production_ready = False
    def __init__(self):
        self.calls = []
        self.tasks = {}
    async def publish(self, **task):
        self.calls.append(deepcopy(task))
        self.tasks.setdefault(task['task_id'], deepcopy(task))


@pytest.fixture
async def env(monkeypatch):
    if os.environ.get('FIRESTORE_EMULATOR_HOST') != HOST:
        pytest.fail('Exact local Firestore emulator required; no remote fallback')
    # Firestore gRPC uses native sockets. Guard channel creation as well as
    # Python sockets; explicitly disable proxy routing on the only allowed host.
    insecure = grpc.aio.insecure_channel
    def local_channel(target, *args, **kwargs):
        assert target == HOST
        kwargs['options'] = tuple(kwargs.get('options', ())) + (('grpc.enable_http_proxy', 0),)
        return insecure(target, *args, **kwargs)
    def forbidden(*a, **k):
        raise AssertionError('External network forbidden')
    monkeypatch.setattr(grpc.aio, 'insecure_channel', local_channel)
    monkeypatch.setattr(grpc.aio, 'secure_channel', forbidden)
    monkeypatch.setattr(grpc, 'secure_channel', forbidden)
    monkeypatch.setattr(grpc, 'insecure_channel', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)
    monkeypatch.setattr('socket.socket.connect_ex', forbidden)
    client = AsyncClient(project='demo-durable-' + uuid4().hex[:12],
                         credentials=AnonymousCredentials())
    assert client._target == HOST
    backend = FirestoreAtomicBackend(client)
    clock = SimpleNamespace(now=1000.)
    core = DurableCore(JobRepository(backend), clock=lambda: clock.now)
    auth = AuthorizedStart('owner', 'project')
    start = await core.start(auth, 'test-key', {'fixture': True})
    async def job():
        return await core.repo.get(start['jobId'])
    async def envelope():
        j = await job()
        return Envelope(j['jobId'], j['revision'], j['dispatchIntent']['operationId'])
    async def claim(owner='worker'):
        return await core.claim(await envelope(), owner)
    yield SimpleNamespace(client=client, backend=backend, core=core, clock=clock,
        auth=auth, start=start, job=job, envelope=envelope, claim=claim,
        job_id=start['jobId'])
    async def delete_document(ref):
        async for collection in ref.collections(retry=None, timeout=3):
            async for child in collection.list_documents(retry=None, timeout=3):
                await delete_document(child)
        await ref.delete(retry=None, timeout=3)
    try:
        async for collection in client.collections(retry=None, timeout=3):
            async for ref in collection.list_documents(retry=None, timeout=3):
                await delete_document(ref)
        for name in ('durableJobs', 'estimates', 'projects'):
            assert [s async for s in client.collection(name).stream(retry=None, timeout=3)] == []
    finally:
        await client._firestore_api.transport.close()


async def success_stage(env, output=None):
    await env.core.complete(await env.claim(), output or {'fixture': True})
    await env.core.complete(await env.claim(), {'score': 95, 'passed': True})


@pytest.mark.asyncio
async def test_start_and_concurrent_idempotency(env):
    responses = await asyncio.gather(*(env.core.start(env.auth, 'test-key', {'fixture': True}) for _ in range(3)))
    assert responses == [env.start] * 3
    jobs = [s async for s in env.client.collection('durableJobs').stream()]
    assert len(jobs) == 1 and jobs[0].to_dict()['dispatchIntent']['status'] == 'pending'
    estimates = [s async for s in env.client.collection('estimates').stream()]
    assert len(estimates) == 1 and (await env.job())['generation'] == 0


@pytest.mark.parametrize('owner,project,inputs', [('other','project',{'fixture':True}),
    ('owner','other',{'fixture':True}), ('owner','project',{})])
@pytest.mark.asyncio
async def test_conflicting_start_rejected(env, owner, project, inputs):
    with pytest.raises(Rejected):
        await env.core.start(AuthorizedStart(owner,project), 'test-key', inputs)


@pytest.mark.asyncio
async def test_actual_batch_start_rolls_back_on_estimate_create_conflict(env):
    import hashlib
    suffix = hashlib.sha256(b'conflict-key').hexdigest()
    await env.client.document('estimates/est-' + suffix).create({'fixture': 'existing'}, retry=None, timeout=3)
    with pytest.raises(Conflict):
        await env.core.start(env.auth, 'conflict-key', {})
    assert not (await env.client.document('durableJobs/job-' + suffix).get()).exists


@pytest.mark.asyncio
async def test_concurrent_worker_claim_active_lease_and_wrong_operation(env):
    e = await env.envelope()
    assert await env.core.claim(replace(e, operation_id='wrong'), 'w') is None
    assert await env.core.claim(replace(e, revision=5), 'w') is None
    leases = await asyncio.gather(env.claim('a'), env.claim('b'))
    assert sum(lease is not None for lease in leases) == 1
    assert await env.claim('steal') is None


@pytest.mark.asyncio
async def test_crash_before_checkpoint_lease_recovery_and_stale_generation(env):
    old = await env.claim()
    env.clock.now += 361
    new = await env.claim('new')
    assert new.generation > old.generation
    with pytest.raises(Rejected):
        await env.core.complete(old, {'old': True})
    await env.core.complete(new, {'new': True})
    assert await env.core.repo.output((await env.job())['candidateRef']) == {'new': True}


@pytest.mark.asyncio
async def test_atomic_checkpoint_and_next_intent_visible(env):
    lease = await env.claim()
    observed = []
    done = asyncio.Event()
    async def observe():
        while not done.is_set():
            j = await env.job()
            observed.append(j)
    watcher = asyncio.create_task(observe())
    try:
        await env.core.complete(lease, {'candidate': True})
    finally:
        done.set()
        await watcher
    observed.append(await env.job())
    for job in observed:
        if job['revision'] == 1:
            assert job['dispatchIntent']['revision'] == 1
            assert job['dispatchIntent']['status'] == 'pending'
            assert (await env.client.document(job['candidateRef']).get()).exists
    assert (await env.job())['currentOperation'] == 'scorer'


@pytest.mark.asyncio
async def test_failed_checkpoint_batch_cannot_advance_job(env):
    lease = await env.claim()
    path = f'durableJobs/{env.job_id}/checkpoints/{lease.envelope.operation_id}'
    await env.client.document(path).create({'output': {'sentinel': True}})
    with pytest.raises(Conflict):
        await env.core.complete(lease, {'candidate': True})
    job = await env.job()
    assert job['revision'] == 0 and job['candidateRef'] is None
    assert job['dispatchIntent']['revision'] == 0


@pytest.mark.asyncio
async def test_checkpoint_ack_loss_no_rerun(env):
    envelope = await env.envelope()
    lease = await env.claim()
    await env.core.complete(lease, {'accepted': True})
    assert await env.core.claim(envelope, 'redelivery') is None
    with pytest.raises(Rejected):
        await env.core.complete(lease, {'duplicate': True})


@pytest.mark.asyncio
async def test_duplicate_scanners_and_delivery_visibility_window(env):
    dispatch = FakeDispatcher()
    scanners = [IntentScanner(env.core, dispatch) for _ in range(2)]
    await asyncio.gather(*(s.run_page() for s in scanners))
    assert len(dispatch.tasks) == 1
    before = len(dispatch.calls)
    await scanners[0].run_page()
    assert len(dispatch.calls) == before  # Queued != abandoned immediately.
    env.clock.now += 361
    await scanners[0].run_page()
    assert len(dispatch.tasks) == 2
    assert (await env.job())['dispatchIntent']['publication'] == 1


@pytest.mark.asyncio
async def test_scanner_crash_before_mark_and_republish(env, monkeypatch):
    dispatch = FakeDispatcher()
    original = env.core.repo.transact
    async def crash(*args):
        raise RuntimeError('fake publication crash')
    async def publish(**task):
        await dispatch.publish(**task)
        monkeypatch.setattr(env.core.repo, 'transact', crash)
    result = await IntentScanner(env.core, SimpleNamespace(publish=publish)).run_page()
    assert result['errors'] == 1
    assert (await env.job())['dispatchIntent']['status'] == 'pending'
    monkeypatch.setattr(env.core.repo, 'transact', original)
    await IntentScanner(env.core, dispatch).run_page()
    assert len(dispatch.calls) == 2 and len(dispatch.tasks) == 1


@pytest.mark.asyncio
async def test_scanner_pagination_and_active_lease_skip(env):
    await env.core.start(env.auth, 'second-key', {})
    await env.claim()
    dispatch = FakeDispatcher()
    scanner = IntentScanner(env.core, dispatch)
    first = await scanner.run_page(limit=1)
    assert first['cursor'] is not None
    second = await scanner.run_page(limit=1, after=first['cursor'])
    third = await scanner.run_page(limit=1, after=second['cursor'])
    assert third['cursor'] is None
    assert len(dispatch.tasks) == 1


@pytest.mark.asyncio
async def test_stale_outer_write_token_and_cost_identity_guard(env):
    boundary = DurableOutputBoundary(env.core, allowed_principal='sa', audience='worker')
    identity = VerifiedService('sa', 'worker')
    old = DurableWriteToken.from_lease(await env.claim())
    env.clock.now += 361
    current = DurableWriteToken.from_lease(await env.claim('new'))
    for token in (old, replace(current, attempt=100), replace(current, generation=100)):
        with pytest.raises(Rejected):
            await boundary.commit(identity, token, {'bad': True})
    await boundary.commit(identity, current, {'good': True})
    await env.core.complete(await env.claim(), {'score':95,'passed':True})
    for _ in range(2):
        await success_stage(env)
    token = DurableWriteToken.from_lease(await env.claim())
    with pytest.raises(Rejected):
        await boundary.commit(identity, replace(token, cost_attempt=None), {'bad': True})
    await env.core.revoke_cost(token.lease())
    with pytest.raises(Rejected):
        await boundary.commit(identity, token, {'bad': True})


@pytest.mark.parametrize('uid,allowed', [('owner',True),('editor',True),('viewer',False),('attacker',False)])
@pytest.mark.asyncio
async def test_authenticated_start_existing_project_policy(env, monkeypatch, uid, allowed):
    await env.client.document('projects/project').set({'ownerId':'owner','collaborators':[
        {'userId':'editor','role':'editor'},{'userId':'viewer','role':'viewer'}]})
    # Verification boundary is mocked only; authorization reads real Firestore.
    monkeypatch.setattr(request_auth.auth, 'verify_id_token', lambda token: {'uid':uid})
    request = Request(EnvironBuilder(method='POST', json={'projectId':'project',
        'idempotencyKey':'authenticated-key','clarificationOutput':{}},
        headers={'Authorization':'Bearer fake-offline-token'}).get_environ())
    service = DurableStartService(env.core)
    if allowed:
        result = await service.handle(request)
        assert result['httpStatus'] == 202 and result['status'] == 'accepted'
        job = await env.core.repo.get(result['jobId'])
        assert job['ownerUid'] == uid and job['generation'] == 0 and job['checkpoints'] == {}
    else:
        with pytest.raises(request_auth.AccessError) as error:
            await service.handle(request)
        assert error.value.status == 403


@pytest.mark.asyncio
async def test_complete_durable_firestore_pipeline_critic_and_final_ack_loss(env):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/authoritative_cost_contract.json').read_text())
    worker = Worker(env.core, allowed_principal='sa', audience='worker')
    identity = VerifiedService('sa','worker')
    dispatch = FakeDispatcher(); scanner = IntentScanner(env.core, dispatch)
    calls = []
    async def execute(job, lease):
        calls.append((job['currentStage'], job['currentOperation']))
        if job['currentOperation'] == 'scorer':
            passed = not (job['currentStage'] == 'scope' and job['qualityAttempt'] == 0)
            return {'score':95 if passed else 35, 'passed':passed}
        if job['currentOperation'] == 'critic':
            return {'feedback':'offline fixture'}
        return fixture if job['currentStage'] == 'final' else {'fixture':job['currentStage']}
    for _ in range(30):
        if (await env.job())['status'] == 'final':
            break
        result = await scanner.run_page()
        assert result['errors'] == 0
        body = (await env.envelope()).to_dict()
        await worker.handle(identity, body, lease_owner='w', execute=execute)
        await worker.handle(identity, body, lease_owner='duplicate', execute=execute)
    job = await env.job()
    assert job['status'] == 'final' and job['progress'] == 100
    assert set(job['checkpoints']) == set(AGENT_SEQUENCE)
    assert len(calls) == 17 and ('scope','critic') in calls
    root = (await env.client.document(f"estimates/{job['estimateId']}").get()).to_dict()
    assert {k: root[k] for k in fixture} == fixture and root['status'] == 'final'
    project_status = (await env.client.document('projects/project/pipeline/status').get()).to_dict()
    assert project_status['status'] == 'complete'
    assert project_status['completedStages'] == AGENT_SEQUENCE
    assert job['dispatchIntent']['status'] == 'completed'
    before = deepcopy(job)
    await worker.handle(identity, body, lease_owner='lost-ack', execute=execute)
    assert await env.job() == before
    assert (await scanner.run_page())['published'] == 0


@pytest.mark.asyncio
async def test_quota_terminal_and_stale_success(env):
    old = await env.claim()
    env.clock.now += 361
    current = await env.claim('current')
    await env.core.fail(current, StructuredError(ErrorCode.LLM_QUOTA_EXCEEDED))
    with pytest.raises(Rejected):
        await env.core.complete(old, {'late':True})
    job = await env.job()
    assert job['lastError']['code'] == ErrorCode.LLM_QUOTA_EXCEEDED
    assert job['status'] == 'failed' and job['dispatchIntent']['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_simultaneous_first_creation_forced_server_contention(env, monkeypatch):
    import hashlib
    path = 'durableJobs/job-' + hashlib.sha256(b'concurrent-new').hexdigest()
    original_read, original_commit = env.backend.read, env.backend.commit
    barrier = asyncio.Event(); reads = 0; conflicts = []
    async def read(target):
        nonlocal reads
        snapshot = await original_read(target)
        if target == path and reads < 2:
            reads += 1
            if reads == 2:
                barrier.set()
            await barrier.wait()
        return snapshot
    async def commit(target, *args):
        try:
            return await original_commit(target, *args)
        except Conflict:
            conflicts.append(target)
            raise
    monkeypatch.setattr(env.backend, 'read', read)
    monkeypatch.setattr(env.backend, 'commit', commit)
    responses = await asyncio.gather(*(env.core.start(env.auth, 'concurrent-new', {}) for _ in range(2)))
    assert responses[0] == responses[1] and path in conflicts
    assert (await env.client.document(path).get()).to_dict()['revision'] == 0


@pytest.mark.asyncio
async def test_duplicate_expired_worker_scanners_share_recovery_publication(env):
    dispatch = FakeDispatcher()
    await IntentScanner(env.core, dispatch).run_page()
    await env.claim()
    env.clock.now += 361
    await asyncio.gather(*(IntentScanner(env.core, dispatch).run_page() for _ in range(2)))
    assert (await env.job())['dispatchIntent']['publication'] == 1
    assert len(dispatch.tasks) == 2


@pytest.mark.asyncio
async def test_scanner_timeout_preserves_pending_intent(env):
    async def slow(**task):
        await asyncio.Event().wait()
    scanner = IntentScanner(env.core, SimpleNamespace(publish=slow), budget_seconds=0.05)
    result = await scanner.run_page()
    assert result['timedOut'] is True
    assert (await env.job())['dispatchIntent']['status'] == 'pending'


@pytest.mark.asyncio
async def test_outer_boundary_cannot_overwrite_accepted_output_after_next_stage(env):
    boundary = DurableOutputBoundary(env.core, allowed_principal='sa', audience='worker')
    identity = VerifiedService('sa','worker')
    token = DurableWriteToken.from_lease(await env.claim())
    await boundary.commit(identity, token, {'accepted':True})
    await env.core.complete(await env.claim(), {'score':95,'passed':True})
    with pytest.raises(Rejected):
        await boundary.commit(identity, token, {'late':True})
    job = await env.job()
    doc = await env.client.document(f"estimates/{job['estimateId']}/agentOutputs/location").get()
    assert doc.to_dict()['output'] == {'accepted':True}


@pytest.mark.asyncio
async def test_finalization_batch_rollback_then_single_completion(env):
    for _ in range(6):
        await success_stage(env)
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/authoritative_cost_contract.json').read_text())
    await env.core.complete(await env.claim(), fixture)
    lease = await env.claim()
    path = f'durableJobs/{env.job_id}/checkpoints/{lease.envelope.operation_id}'
    blocker = env.client.document(path)
    await blocker.create({'output':{}})
    with pytest.raises(Conflict):
        await env.core.complete(lease, {'score':95,'passed':True})
    job = await env.job()
    root = (await env.client.document(f"estimates/{job['estimateId']}").get()).to_dict()
    assert job['status'] == 'running' and 'finalEstimate' not in root
    await blocker.delete()
    await env.core.complete(lease, {'score':95,'passed':True})
    assert (await env.job())['status'] == 'final'

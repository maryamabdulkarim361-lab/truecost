"""Atomic CAS fake, fake clock and mocked operations. No networking permitted."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from config.errors import StructuredError, ErrorCode, A2AError
from services.durable_store import (Snapshot, Conflict, JobRepository, FirestoreAtomicBackend)
from services.durable_execution import (DurableCore, AuthorizedStart, VerifiedService,
    Worker, Envelope, Rejected, AGENT_SEQUENCE)

pytestmark = pytest.mark.usefixtures('block_network')


class MemoryBackend:
    def __init__(self):
        self.docs, self.versions = {}, {}
        self.fail_commit = False
        self.commits = 0

    async def read(self, path):
        await asyncio.sleep(0)  # Force competing readers to observe same version.
        return Snapshot(deepcopy(self.docs.get(path)), self.versions.get(path))

    async def commit(self, path, version, data, writes):
        # Single event-loop atomic section; all checks before any mutation.
        if self.fail_commit:
            raise RuntimeError('simulated storage failure')
        if self.versions.get(path) != version:
            raise Conflict()
        if any((w.create and w.path in self.docs) or (w.version is not None and self.versions.get(w.path) != w.version) for w in writes):
            raise Conflict()
        docs = deepcopy(self.docs)
        docs[path] = deepcopy(data)
        for w in writes:
            docs.setdefault(w.path, {}).update(deepcopy(w.data))
        self.docs = docs
        self.versions[path] = (version or 0) + 1
        for w in writes:
            self.versions[w.path] = self.versions.get(w.path, 0) + 1
        self.commits += 1


class Queue:
    production_ready = False
    def __init__(self):
        self.messages = []
    async def publish(self, **message):
        self.messages.append(deepcopy(message))


@pytest.fixture
async def env():
    backend = MemoryBackend()
    clock = SimpleNamespace(now=1000.)
    repo = JobRepository(backend)
    core = DurableCore(repo, clock=lambda: clock.now)
    auth = AuthorizedStart('owner', 'project')
    start = await core.start(auth, 'stable-key', {'fixture': True})
    job_id = start['jobId']
    async def job():
        return await repo.get(job_id)
    async def envelope():
        j = await job()
        return Envelope(job_id, j['revision'], j['dispatchIntent']['operationId'])
    async def claim(owner='worker'):
        return await core.claim(await envelope(), owner)
    return SimpleNamespace(backend=backend, clock=clock, repo=repo, core=core,
        auth=auth, start=start, job_id=job_id, job=job, envelope=envelope, claim=claim)


async def succeed_stage(env, output=None):
    await env.core.complete(await env.claim(), output or {'fixture': True})
    await env.core.complete(await env.claim(), {'score': 95, 'passed': True})


@pytest.mark.asyncio
async def test_new_job_and_atomic_start_intent(env):
    job = await env.job()
    assert job['status'] == 'queued' and job['currentStage'] == 'location'
    assert job['dispatchIntent']['status'] == 'pending'
    assert job['checkpoints'] == {} and job['generation'] == 0
    assert env.backend.docs[f"estimates/{job['estimateId']}"]['durableJobId'] == env.job_id
    assert env.backend.commits == 1
    assert env.start['httpStatus'] == 202


@pytest.mark.asyncio
async def test_start_failure_has_no_partial_job_or_estimate():
    backend = MemoryBackend(); backend.fail_commit = True
    with pytest.raises(RuntimeError):
        await DurableCore(JobRepository(backend)).start(AuthorizedStart('u','p'), 'k', {})
    assert backend.docs == {}


@pytest.mark.asyncio
async def test_concurrent_duplicate_start(env):
    starts = await asyncio.gather(*(env.core.start(env.auth, 'stable-key', {'fixture': True}) for _ in range(2)))
    assert starts == [env.start, env.start]
    assert len([p for p in env.backend.docs if p.startswith('durableJobs/')]) == 1


@pytest.mark.parametrize('auth,data', [(AuthorizedStart('other','project'), {'fixture': True}),
    (AuthorizedStart('owner','other'), {'fixture': True}), (AuthorizedStart('owner','project'), {})])
@pytest.mark.asyncio
async def test_start_conflict(env, auth, data):
    with pytest.raises(Rejected, match='conflict'):
        await env.core.start(auth, 'stable-key', data)


@pytest.mark.asyncio
async def test_claim_matches_full_envelope(env):
    e = await env.envelope()
    assert await env.core.claim(Envelope(e.job_id, 1, e.operation_id), 'w') is None
    assert await env.core.claim(Envelope(e.job_id, 0, 'wrong'), 'w') is None
    lease = await env.claim()
    assert lease.generation == 1 and (await env.job())['attempt'] == 1


@pytest.mark.asyncio
async def test_concurrent_claim_only_one_owner(env):
    leases = await asyncio.gather(env.claim('a'), env.claim('b'))
    assert sum(x is not None for x in leases) == 1


@pytest.mark.asyncio
async def test_crash_expiry_recovery_stale_generation(env):
    old = await env.claim()
    assert await env.claim('new') is None
    env.clock.now += 361
    new = await env.claim('new')
    assert new.generation == 2
    with pytest.raises(Rejected, match='Stale'):
        await env.core.complete(old, {'old': True})
    await env.core.complete(new, {'new': True})
    job = await env.job()
    assert await env.repo.output(job['candidateRef']) == {'new': True}


@pytest.mark.asyncio
async def test_expired_lease_cannot_complete_without_takeover(env):
    lease = await env.claim()
    env.clock.now += 361
    with pytest.raises(Rejected):
        await env.core.complete(lease, {})


@pytest.mark.asyncio
async def test_checkpoint_and_next_intent_atomicity(env):
    lease = await env.claim()
    before = deepcopy(env.backend.docs)
    env.backend.fail_commit = True
    with pytest.raises(RuntimeError):
        await env.core.complete(lease, {'valid': True})
    assert env.backend.docs == before
    env.backend.fail_commit = False
    await env.core.complete(lease, {'valid': True})
    job = await env.job()
    assert job['revision'] == 1 and job['currentOperation'] == 'scorer'
    assert job['dispatchIntent']['status'] == 'pending'
    assert job['candidateRef'] in env.backend.docs


@pytest.mark.asyncio
async def test_crash_after_checkpoint_before_ack(env):
    envelope = await env.envelope()
    lease = await env.claim()
    await env.core.complete(lease, {'valid': True})
    assert await env.core.claim(envelope, 'redelivery') is None
    with pytest.raises(Rejected):
        await env.core.complete(lease, {'duplicate': True})


@pytest.mark.asyncio
async def test_duplicate_publication_has_same_task_and_no_duplicate_commit(env):
    queue = Queue()
    await asyncio.gather(env.core.publish(env.job_id, queue), env.core.publish(env.job_id, queue))
    assert len(queue.messages) == 2 and queue.messages[0] == queue.messages[1]
    assert (await env.job())['dispatchIntent']['status'] == 'dispatched'


@pytest.mark.asyncio
async def test_dispatch_failure_retains_intent(env):
    with pytest.raises(Rejected, match='not configured'):
        await env.core.publish(env.job_id)
    assert (await env.job())['dispatchIntent']['status'] == 'pending'


def test_production_missing_or_fake_dispatcher_fails_closed():
    for dispatcher in (None, Queue()):
        with pytest.raises(Rejected):
            DurableCore(JobRepository(MemoryBackend()), production=True, dispatcher=dispatcher)


@pytest.mark.asyncio
async def test_recovery_task_name_changes_for_queue_tombstones(env):
    queue = Queue()
    await env.core.publish(env.job_id, queue)
    await env.claim()
    env.clock.now += 361
    await env.core.recover(env.job_id)
    await env.core.publish(env.job_id, queue)
    first, second = queue.messages[0]['payload'], queue.messages[1]['payload']
    assert first['operationId'] == second['operationId']
    assert first['generation'] == 0 and second['generation'] == 1
    assert queue.messages[0]['task_id'] != queue.messages[1]['task_id']


@pytest.mark.asyncio
async def test_bounded_crash_redelivery_exhaustion(env):
    for _ in range(3):
        assert await env.claim() is not None
        env.clock.now += 361
    await env.core.recover(env.job_id)
    assert (await env.job())['status'] == 'failed'
    assert await env.claim() is None


@pytest.mark.parametrize('code', [ErrorCode.LLM_QUOTA_EXCEEDED, ErrorCode.LLM_AUTH_ERROR,
    ErrorCode.LLM_INVALID_RESPONSE, ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_PROVIDER_ERROR,
    ErrorCode.INSUFFICIENT_DATA])
@pytest.mark.asyncio
async def test_nonretryable_structured_failure(env, code):
    lease = await env.claim()
    await env.core.fail(lease, StructuredError(code, provider='gemini', http_status=429))
    job = await env.job()
    assert job['status'] == 'failed' and job['lastError']['code'] == code
    assert job['currentOperation'] == 'primary' and job['qualityAttempt'] == 0
    assert job['dispatchIntent']['status'] == 'cancelled'
    assert job['checkpoints'] == {}


@pytest.mark.asyncio
async def test_transient_rate_limit_only_once_and_respects_schedule(env):
    error = StructuredError(ErrorCode.LLM_RATE_LIMITED, retry_delay=2)
    await env.core.fail(await env.claim(), error)
    job = await env.job()
    assert job['providerRetries'] == 1 and job['qualityAttempt'] == 0
    assert await env.claim() is None
    env.clock.now += 2
    await env.core.fail(await env.claim(), error)
    assert (await env.job())['status'] == 'failed'


@pytest.mark.parametrize('delay', [None, 16])
@pytest.mark.asyncio
async def test_rate_limit_without_bounded_delay_stops(env, delay):
    await env.core.fail(await env.claim(), StructuredError(ErrorCode.LLM_RATE_LIMITED, retry_delay=delay))
    assert (await env.job())['status'] == 'failed'


@pytest.mark.asyncio
async def test_errors_reserialized_without_arbitrary_metadata(env):
    error = StructuredError(ErrorCode.LLM_PROVIDER_ERROR)
    error.message = 'FAKE_SECRET_URL_BODY'
    error.details.update({'headers': 'FAKE_SECRET_URL_BODY', 'reason': 'FAKE_SECRET_URL_BODY'})
    await env.core.fail(await env.claim(), error)
    assert 'FAKE_SECRET_URL_BODY' not in repr(env.backend.docs)


@pytest.mark.asyncio
async def test_low_quality_scorer_critic_retry_preserved(env):
    for attempt in range(3):
        assert (await env.job())['qualityAttempt'] == attempt
        await env.core.complete(await env.claim(), {'candidate': attempt})
        await env.core.complete(await env.claim(), {'score': 35, 'passed': False})
        if attempt < 2:
            assert (await env.job())['currentOperation'] == 'critic'
            await env.core.complete(await env.claim(), {'feedback': 'fixture'})
    job = await env.job()
    assert job['status'] == 'failed' and job['checkpoints'] == {}


@pytest.mark.asyncio
async def test_nonstructured_scorer_fallback_and_primary_retry(env):
    error = A2AError(ErrorCode.A2A_TIMEOUT, 'fake transport', target_agent='location')
    await env.core.execution_failure(await env.claim(), error)
    assert (await env.job())['qualityAttempt'] == 1
    await env.core.complete(await env.claim(), {'fixture': True})
    await env.core.execution_failure(await env.claim(), error)
    assert (await env.job())['checkpoints']['location']['score'] == 80


@pytest.mark.asyncio
async def test_cost_inner_revocation_and_outer_generation(env):
    for _ in range(3):
        await succeed_stage(env)
    old = await env.claim()
    assert old.cost_attempt
    await env.core.revoke_cost(old)
    with pytest.raises(Rejected, match='Cost'):
        await env.core.complete(old, {'stale': True})
    env.clock.now += 361
    new = await env.claim('new')
    assert new.cost_attempt != old.cost_attempt
    with pytest.raises(Rejected):
        await env.core.complete(old, {'stale': True})
    await env.core.complete(new, {'acceptedCost': True})
    await env.core.complete(await env.claim(), {'score': 95, 'passed': True})
    assert (await env.job())['currentStage'] == 'risk'
    with pytest.raises(Rejected):
        await env.core.complete(old, {'overwrite': True})


@pytest.mark.asyncio
async def test_cost_expired_inner_attempt_cannot_save_but_can_fail(env):
    for _ in range(3):
        await succeed_stage(env)
    lease = await env.claim()
    env.clock.now += 241
    with pytest.raises(Rejected, match='Cost'):
        await env.core.complete(lease, {})
    await env.core.fail(lease, StructuredError(ErrorCode.LLM_TIMEOUT))
    assert (await env.job())['status'] == 'failed'


@pytest.mark.asyncio
async def test_worker_auth_and_envelope_reject_untrusted_fields(env):
    worker = Worker(env.core, allowed_principal='worker-sa', audience='private-worker')
    execute = AsyncMock()
    body = (await env.envelope()).to_dict()
    for identity in (None, VerifiedService('other', 'private-worker'), VerifiedService('worker-sa', 'wrong')):
        with pytest.raises(Rejected):
            await worker.handle(identity, body, lease_owner='w', execute=execute)
    with pytest.raises(Rejected):
        await worker.handle(VerifiedService('worker-sa', 'private-worker'),
            {**body, 'ownerUid': 'forged'}, lease_owner='w', execute=execute)
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_complete_durable_seven_stage_money_and_finalization_once(env):
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/authoritative_cost_contract.json').read_text())
    worker = Worker(env.core, allowed_principal='worker-sa', audience='private-worker')
    identity = VerifiedService('worker-sa', 'private-worker')
    calls = []
    async def execute(job, lease):
        calls.append((job['currentStage'], job['currentOperation']))
        if job['currentOperation'] == 'scorer':
            return {'score': 95, 'passed': True}
        return fixture if job['currentStage'] == 'final' else {'fixture': job['currentStage']}
    queue = Queue()
    while (await env.job())['status'] != 'final':
        await env.core.publish(env.job_id, queue)
        body = queue.messages[-1]['payload']
        await worker.handle(identity, body, lease_owner='w', execute=execute)
        await worker.handle(identity, body, lease_owner='duplicate', execute=execute)
    job = await env.job()
    assert calls == [(s, op) for s in AGENT_SEQUENCE for op in ('primary', 'scorer')]
    assert list(job['checkpoints']) == AGENT_SEQUENCE and job['progress'] == 100
    root = env.backend.docs[f"estimates/{job['estimateId']}"]
    assert {k: root[k] for k in fixture} == fixture
    assert root['status'] == 'final'
    assert root['finalEstimate'] == root['totalCost'] == 31456.74
    assert root['p50'] == 29283.60 and root['p80'] == 32797.63 and root['p90'] == 35067.77
    assert env.backend.docs['projects/project/pipeline/status']['status'] == 'complete'
    before = deepcopy(env.backend.docs)
    await worker.handle(identity, body, lease_owner='lost-ack', execute=execute)
    assert env.backend.docs == before


@pytest.mark.asyncio
async def test_worker_cancellation_keeps_recoverable_lease(env):
    worker = Worker(env.core, allowed_principal='sa', audience='worker')
    async def cancel(job, lease):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await worker.handle(VerifiedService('sa','worker'), (await env.envelope()).to_dict(),
            lease_owner='w', execute=cancel)
    assert (await env.job())['status'] == 'running'
    env.clock.now += 361
    assert await env.claim('recover') is not None


@pytest.mark.asyncio
async def test_firestore_adapter_atomic_batch_precondition_and_no_retries():
    client = MagicMock()
    batch = client.batch.return_value
    batch.commit = AsyncMock()
    from services.durable_store import Write
    adapter = FirestoreAtomicBackend(client)
    await adapter.commit('durableJobs/job', 'version', {'revision': 1}, [Write('checkpoints/op', {'ok': True}, True)])
    client.write_option.assert_called_once_with(last_update_time='version')
    batch.update.assert_called_once()
    batch.create.assert_called_once()
    batch.commit.assert_awaited_once_with(retry=None, timeout=3)


@pytest.mark.asyncio
async def test_existing_cost_fence_methods_interoperate_with_core(env, monkeypatch):
    from services.firestore_service import FirestoreService
    from config.errors import TrueCostError
    for _ in range(3):
        await succeed_stage(env)
    lease = await env.claim()
    job = await env.job()
    root_path = f"estimates/{job['estimateId']}"
    legacy = FirestoreService(db=object())
    # Inject storage only; run the unchanged legacy fencing/revocation callbacks.
    async def mutate(estimate_id, callback):
        snapshot = await env.backend.read(root_path)
        mutation = callback(deepcopy(snapshot.data))
        if mutation is None:
            return
        updates, writes = mutation
        assert not writes
        root = deepcopy(snapshot.data)
        for key, value in updates.items():
            if '.' in key:
                parent, field = key.split('.')
                root[parent][field] = value
            else:
                root[key] = value
        await env.backend.commit(root_path, snapshot.version, root, [])
    monkeypatch.setattr(legacy, '_mutate_cost', mutate)
    monkeypatch.setattr('services.firestore_service.time.time', lambda: env.clock.now)
    await legacy._write_cost_attempt(job['estimateId'], lease.cost_attempt, {'probe': 'accepted'})
    await legacy.end_cost_attempt(job['estimateId'], lease.cost_attempt)
    with pytest.raises(Rejected, match='Cost'):
        await env.core.complete(lease, {'late': True})
    with pytest.raises(TrueCostError):
        await legacy._write_cost_attempt(job['estimateId'], lease.cost_attempt, {'probe': 'late'})
    env.clock.now += 361
    fresh = await env.claim('fresh')
    with pytest.raises(TrueCostError):
        await legacy._write_cost_attempt(job['estimateId'], lease.cost_attempt, {'probe': 'old'})
    await env.core.complete(fresh, {'cost': 'current'})
    await env.core.complete(await env.claim(), {'score': 95, 'passed': True})
    assert env.backend.docs[root_path]['costOutput'] == {'cost': 'current'}


@pytest.mark.asyncio
async def test_terminal_failure_blocks_old_success(env):
    old = await env.claim()
    env.clock.now += 361
    fresh = await env.claim('fresh')
    await env.core.fail(fresh, StructuredError(ErrorCode.LLM_AUTH_ERROR))
    before = deepcopy(env.backend.docs)
    with pytest.raises(Rejected):
        await env.core.complete(old, {'late': True})
    assert env.backend.docs == before


@pytest.mark.asyncio
async def test_final_transaction_failure_does_not_partially_finalize(env):
    for _ in range(6):
        await succeed_stage(env)
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/authoritative_cost_contract.json').read_text())
    await env.core.complete(await env.claim(), fixture)
    lease = await env.claim()
    before = deepcopy(env.backend.docs)
    env.backend.fail_commit = True
    with pytest.raises(RuntimeError):
        await env.core.complete(lease, {'score': 95, 'passed': True})
    assert env.backend.docs == before
    env.backend.fail_commit = False
    await env.core.complete(lease, {'score': 95, 'passed': True})
    assert (await env.job())['status'] == 'final'


@pytest.mark.asyncio
async def test_structured_scorer_failure_does_not_call_critic(env):
    await env.core.complete(await env.claim(), {'fixture': True})
    await env.core.execution_failure(await env.claim(), StructuredError(ErrorCode.LLM_QUOTA_EXCEEDED))
    job = await env.job()
    assert job['status'] == 'failed' and job['currentOperation'] == 'scorer'
    assert job['checkpoints'] == {} and job['qualityAttempt'] == 0


@pytest.mark.asyncio
async def test_existing_critic_transport_fallback_preserved(env):
    await env.core.complete(await env.claim(), {'fixture': True})
    await env.core.complete(await env.claim(), {'score': 35, 'passed': False})
    await env.core.execution_failure(await env.claim(), A2AError(
        ErrorCode.A2A_TIMEOUT, 'fake', target_agent='location_critic'))
    job = await env.job()
    assert job['currentOperation'] == 'primary' and job['qualityAttempt'] == 1
    assert (await env.repo.output(job['criticRef']))['score'] == 35


@pytest.mark.asyncio
async def test_publish_crash_after_enqueue_keeps_recoverable_intent(env):
    queue = Queue()
    async def publish(**message):
        queue.messages.append(message)
        env.backend.fail_commit = True
    with pytest.raises(RuntimeError):
        await env.core.publish(env.job_id, SimpleNamespace(publish=publish))
    assert len(queue.messages) == 1
    assert (await env.job())['dispatchIntent']['status'] == 'pending'
    env.backend.fail_commit = False
    await env.core.publish(env.job_id, queue)
    assert queue.messages[0]['task_id'] == queue.messages[1]['task_id']


@pytest.mark.asyncio
async def test_firestore_create_batch_and_conflict_mapping():
    from google.api_core.exceptions import FailedPrecondition
    client = MagicMock()
    batch = client.batch.return_value
    batch.commit = AsyncMock(side_effect=FailedPrecondition('fake conflict'))
    with pytest.raises(Conflict):
        await FirestoreAtomicBackend(client).commit('durableJobs/job', None, {}, [])
    batch.create.assert_called_once()
    batch.commit.assert_awaited_once_with(retry=None, timeout=3)


@pytest.mark.asyncio
async def test_cost_scorer_cannot_promote_after_external_revocation_identity_change(env):
    for _ in range(3):
        await succeed_stage(env)
    await env.core.complete(await env.claim(), {'cost': 'candidate'})
    lease = await env.claim()
    job = await env.job()
    root = f"estimates/{job['estimateId']}"
    snapshot = await env.backend.read(root)
    data = deepcopy(snapshot.data)
    data['costAttempt'] = {'id': 'newer-external-attempt', 'active': True, 'expiresAt': 9999}
    await env.backend.commit(root, snapshot.version, data, [])
    with pytest.raises(Rejected, match='Cost'):
        await env.core.complete(lease, {'score': 95, 'passed': True})
    assert 'costOutput' not in env.backend.docs[root]


@pytest.mark.parametrize('operation,lease', [(300,360), (360,360), (400,540)])
def test_worker_bounds_cannot_collapse_timeout_hierarchy(operation, lease):
    with pytest.raises(ValueError):
        DurableCore(JobRepository(MemoryBackend()), operation_seconds=operation, lease_seconds=lease)

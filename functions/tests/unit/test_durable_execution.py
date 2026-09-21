"""No cloud adapters: transactional contract model + fake queue/clock only."""
import asyncio
from copy import deepcopy
import pytest
from prototypes.durable_execution import Claim, Coordinator

pytestmark = pytest.mark.usefixtures('block_network')
STAGES = ('location', 'scope', 'code_compliance', 'cost', 'risk', 'timeline', 'final')


class MemoryStore:
    # No await inside mutations: atomic ONLY in this single-threaded test model.
    def __init__(self):
        self.jobs = {}
        self.now = 0

    async def create_or_get(self, job_id, owner, fingerprint):
        job = self.jobs.setdefault(job_id, dict(owner=owner, fingerprint=fingerprint,
            revision=0, epoch=0, lease=0, state='pending', outputs={}, error=None))
        if (job['owner'], job['fingerprint']) != (owner, fingerprint):
            raise ValueError('Identity conflict')
        return None if job['state'] in ('completed', 'failed') else job['revision']

    async def claim(self, job_id, revision):
        job = self.jobs[job_id]
        if job['state'] in ('completed', 'failed') or revision != job['revision'] or job['lease'] > self.now:
            return None
        job.update(epoch=job['epoch'] + 1, lease=self.now + 10, state='running')
        return Claim(job_id, revision, job['epoch'])

    async def finish(self, claim, output, error):
        job = self.jobs[claim.job_id]
        if (job['state'] != 'running' or job['revision'] != claim.revision
                or job['epoch'] != claim.epoch or job['lease'] <= self.now):
            raise ValueError('Stale claim')
        if error:
            job.update(state='failed', error=error, lease=0)
            return None
        job['outputs'][STAGES[claim.revision]] = deepcopy(output)
        job.update(revision=claim.revision + 1, lease=0, state='pending')
        if job['revision'] == len(STAGES):
            job['state'] = 'completed'
            return None
        return job['revision']


class Queue:
    def __init__(self):
        self.items = []

    async def enqueue(self, job_id, revision):
        # Intentionally retain duplicates to prove worker fencing, not queue magic.
        self.items.append((job_id, revision))


@pytest.fixture
def setup():
    store, queue = MemoryStore(), Queue()
    return store, queue, Coordinator(store, queue)


@pytest.mark.asyncio
async def test_start_is_immediate_and_duplicate_safe(setup):
    store, queue, service = setup
    assert await service.start('e', 'u', 'input-v1') == {'http_status': 202, 'job_id': 'e'}
    await service.start('e', 'u', 'input-v1')
    assert len(store.jobs) == 1 and store.jobs['e']['outputs'] == {}
    assert queue.items == [('e', 0), ('e', 0)]
    for owner, fingerprint in [('other', 'input-v1'), ('u', 'different')]:
        with pytest.raises(ValueError):
            await service.start('e', owner, fingerprint)


@pytest.mark.asyncio
async def test_seven_stage_success_duplicate_dispatch_and_final_ack_loss(setup):
    store, queue, service = setup
    await service.start('e', 'u', 'v1')
    calls = []
    async def execute(claim):
        calls.append(STAGES[claim.revision])
        return {'fixture': STAGES[claim.revision]}
    while queue.items:
        message = queue.items.pop(0)
        await service.work(*message, execute)
        await service.work(*message, execute)  # lost ACK / redelivery
    assert calls == list(STAGES)
    assert store.jobs['e']['state'] == 'completed'
    assert list(store.jobs['e']['outputs']) == list(STAGES)
    await service.start('e', 'u', 'v1')
    assert queue.items == []


@pytest.mark.asyncio
async def test_crash_lease_recovery_and_stale_attempt(setup):
    store, _, service = setup
    await service.start('e', 'u', 'v1')
    old = await store.claim('e', 0)
    assert await store.claim('e', 0) is None
    store.now = 11
    new = await store.claim('e', 0)
    with pytest.raises(ValueError, match='Stale'):
        await store.finish(old, {'old': True}, None)
    await store.finish(new, {'new': True}, None)
    assert store.jobs['e']['outputs']['location'] == {'new': True}


@pytest.mark.asyncio
async def test_failure_terminal_and_sanitized(setup):
    store, _, service = setup
    await service.start('e', 'u', 'v1')
    async def fail(claim):
        raise RuntimeError('FAKE_PRIVATE_BODY https://example.invalid/private')
    await service.work('e', 0, fail)
    await service.work('e', 0, fail)
    assert store.jobs['e']['state'] == 'failed'
    assert store.jobs['e']['error'] == 'WORK_UNIT_FAILED'
    assert 'FAKE_PRIVATE_BODY' not in repr(store.jobs)


@pytest.mark.asyncio
async def test_missing_dispatcher_fails_closed_and_intent_survives():
    store = MemoryStore()
    with pytest.raises(RuntimeError, match='not configured'):
        await Coordinator(store).start('e', 'u', 'v1')
    assert store.jobs['e']['state'] == 'pending'
    queue = Queue()
    await Coordinator(store, queue).start('e', 'u', 'v1')
    assert queue.items == [('e', 0)]


@pytest.mark.asyncio
async def test_checkpoint_survives_next_dispatch_failure(setup):
    store, _, service = setup
    await service.start('e', 'u', 'v1')
    async def execute(claim):
        return {'fixture': 'scope-context'}
    service.dispatcher = None  # Simulate dispatcher outage after checkpoint.
    with pytest.raises(AttributeError):
        await service.work('e', 0, execute)
    assert store.jobs['e']['revision'] == 1
    queue = Queue()
    await Coordinator(store, queue).start('e', 'u', 'v1')
    assert queue.items == [('e', 1)]


@pytest.mark.asyncio
async def test_cancellation_not_converted_to_terminal_failure(setup):
    store, _, service = setup
    await service.start('e', 'u', 'v1')
    async def cancel(claim):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.work('e', 0, cancel)
    assert store.jobs['e']['state'] == 'running'
    store.now = 11
    assert await store.claim('e', 0) is not None


@pytest.mark.asyncio
async def test_crash_after_scope_resumes_without_repeating_accepted_stages(setup):
    store, queue, service = setup
    await service.start('e', 'u', 'v1')
    calls = []
    async def execute(claim):
        calls.append(STAGES[claim.revision])
        return {'accepted': True}
    await service.work('e', 0, execute)
    await service.work('e', 1, execute)
    # Lose process/queue view. A new coordinator recovers pending intent.
    recovered_queue = Queue()
    recovered = Coordinator(store, recovered_queue)
    await recovered.start('e', 'u', 'v1')
    assert recovered_queue.items == [('e', 2)]
    await recovered.work('e', 2, execute)
    assert calls == ['location', 'scope', 'code_compliance']


@pytest.mark.asyncio
async def test_stale_success_cannot_overwrite_terminal_failure(setup):
    store, _, service = setup
    await service.start('e', 'u', 'v1')
    old = await store.claim('e', 0)
    store.now = 11
    current = await store.claim('e', 0)
    await store.finish(current, None, 'WORK_UNIT_FAILED')
    with pytest.raises(ValueError, match='Stale'):
        await store.finish(old, {'late': 'success'}, None)
    assert store.jobs['e']['state'] == 'failed'
    assert store.jobs['e']['outputs'] == {}

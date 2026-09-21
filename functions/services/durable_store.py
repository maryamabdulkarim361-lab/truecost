"""Versioned atomic persistence for durable execution. No client is created here."""
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


class Conflict(Exception):
    """Optimistic transaction lost; callback may be evaluated again."""


@dataclass
class Snapshot:
    data: dict | None
    version: Any


@dataclass
class Write:
    path: str
    data: dict
    create: bool = False
    version: Any = None


@dataclass
class Mutation:
    job: dict
    result: Any = None
    writes: list[Write] = field(default_factory=list)


class AtomicBackend(Protocol):
    async def read(self, path: str) -> Snapshot: ...
    async def commit(self, path: str, version: Any, data: dict, writes: list[Write]) -> None:
        """CAS parent and ALL writes atomically, including create preconditions."""
        ...


class JobRepository:
    def __init__(self, backend: AtomicBackend):
        self.backend = backend

    async def transact(self, job_id: str, change: Callable[[dict | None], Mutation | None]):
        path = f'durableJobs/{job_id}'
        for _ in range(3):
            snapshot = await self.backend.read(path)
            mutation = change(deepcopy(snapshot.data))
            if mutation is None:
                return None
            try:
                # One write per document, including the final root/project transition.
                # Public estimate projection is derived only from this authoritative job
                # and committed atomically with it; clients never read server-only jobs.
                job = mutation.job
                if job and job.get('estimateId'):
                    mutation.writes.append(Write(f"estimates/{job['estimateId']}", {'pipelineStatus': {
                        'currentAgent': job.get('currentStage'),
                        'completedAgents': [stage for stage in ('location','scope','code_compliance','cost','risk','timeline','final') if stage in job.get('checkpoints', {})],
                        'progress': job.get('progress', 0),
                        'status': job.get('status'),
                        'error': (job.get('lastError') or {}).get('code'),
                    }}))
                grouped = {}
                for write in mutation.writes:
                    if write.path in grouped:
                        previous = grouped[write.path]
                        previous.data.update(deepcopy(write.data))
                        if write.version is not None:
                            previous.version = write.version
                    else:
                        grouped[write.path] = deepcopy(write)
                await self.backend.commit(path, snapshot.version, mutation.job, list(grouped.values()))
            except Conflict:
                continue
            return mutation.result
        raise Conflict('Durable transaction conflicted')

    async def get(self, job_id: str) -> dict | None:
        return (await self.backend.read(f'durableJobs/{job_id}')).data

    async def output(self, path: str) -> dict:
        snapshot = await self.backend.read(path)
        if snapshot.data is None:
            raise ValueError('Checkpoint missing')
        return snapshot.data['output']


class FirestoreAtomicBackend:
    """Injected google.cloud.firestore AsyncClient; no credentials/default client.

    Bounded CAS batches use a parent update-time precondition. Every transition
    writes that parent, so child/checkpoint writes share the same fence. No
    threads, no SDK retries. Emulator validation is separate from cloud validation.
    """
    def __init__(self, async_client):
        self.client = async_client

    async def read(self, path):
        snap = await self.client.document(path).get(retry=None, timeout=3)
        return Snapshot(snap.to_dict() if snap.exists else None,
                        snap.update_time if snap.exists else None)

    async def commit(self, path, version, data, writes):
        from google.api_core.exceptions import Aborted, AlreadyExists, FailedPrecondition
        batch = self.client.batch()
        ref = self.client.document(path)
        if version is None:
            batch.create(ref, data)
        else:
            batch.update(ref, data, option=self.client.write_option(last_update_time=version))
        for write in writes:
            target = self.client.document(write.path)
            if write.create:
                batch.create(target, write.data)
            elif write.version is not None:
                batch.update(target, write.data,
                    option=self.client.write_option(last_update_time=write.version))
            else:
                batch.set(target, write.data, merge=True)
        try:
            await batch.commit(retry=None, timeout=3)
        except (Aborted, AlreadyExists, FailedPrecondition) as error:
            raise Conflict('Durable transaction conflicted') from error

    async def read_many(self, paths):
        """One bounded RPC for ledger promotion; never one timeout per row."""
        if len(paths) > 400:
            raise ValueError('Atomic ledger read limit exceeded')
        refs = [self.client.document(path) for path in paths]
        if not refs:
            return {}
        return {snap.reference.path: Snapshot(snap.to_dict() if snap.exists else None,
                                              snap.update_time if snap.exists else None)
                async for snap in self.client.get_all(refs, retry=None, timeout=3)}

    async def scan_jobs(self, *, limit=25, after=None):
        """Bounded stable page; caller retains returned cursor across invocations.

        No composite index required. Terminal documents count toward the page;
        pagination prevents early terminal IDs starving later active jobs.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Invalid scan page size')
        query = self.client.collection('durableJobs').order_by('__name__').limit(limit)
        if after is not None:
            from services.durable_execution import identifier
            query = query.start_after({'__name__': self.client.document(f'durableJobs/{identifier(after)}')})
        snapshots = [s async for s in query.stream(retry=None, timeout=3)]
        return [s.id for s in snapshots], (snapshots[-1].id if len(snapshots) == limit else None)

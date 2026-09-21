"""Offline design proof, NOT a deployed endpoint or a production store.

Adapters must supply transactional persistence and durable dispatch. Workers run
one bounded work unit; in production that unit is primary, scorer, or critic,
not the entire quality retry loop. Authentication precedes these interfaces.
"""
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class Claim:
    job_id: str
    revision: int
    epoch: int


class Store(Protocol):
    async def create_or_get(self, job_id: str, owner: str, fingerprint: str) -> int | None:
        """Atomically create job AND pending dispatch; reject identity conflicts.

        Return pending revision, or None for terminal jobs.
        """
        ...

    async def claim(self, job_id: str, revision: int) -> Claim | None:
        """Atomic lease acquisition; reject live duplicates/old revisions/terminal."""
        ...

    async def finish(self, claim: Claim, output: Any, error: str | None) -> int | None:
        """Fence epoch/lease/revision; atomically checkpoint AND create next intent.

        On last unit, commit terminal status and finalization together. Rejected
        stale writes must raise; None means a committed terminal transition.
        """
        ...


class Dispatcher(Protocol):
    async def enqueue(self, job_id: str, revision: int) -> None:
        """Durably enqueue deterministic job/revision identity; duplicates safe."""
        ...


class UnconfiguredDispatcher:
    async def enqueue(self, job_id: str, revision: int) -> None:
        raise RuntimeError("Durable dispatcher is not configured")


class Coordinator:
    def __init__(self, store: Store, dispatcher: Dispatcher | None = None):
        self.store = store
        self.dispatcher = dispatcher if dispatcher is not None else UnconfiguredDispatcher()

    async def start(self, job_id: str, owner: str, fingerprint: str) -> dict:
        # Caller has already verified Firebase identity and project authorization.
        revision = await self.store.create_or_get(job_id, owner, fingerprint)
        if revision is not None:
            await self.dispatcher.enqueue(job_id, revision)
        return {"http_status": 202, "job_id": job_id}

    async def work(self, job_id: str, revision: int,
                   execute: Callable[[Claim], Awaitable[Any]]) -> None:
        claim = await self.store.claim(job_id, revision)
        if claim is None:
            return
        try:
            output = await execute(claim)
        except Exception:
            # Prototype uses a fixed code only. Production preserves sanitized
            # StructuredError and existing provider/quality policies separately.
            following = await self.store.finish(claim, None, "WORK_UNIT_FAILED")
        else:
            following = await self.store.finish(claim, output, None)
        if following is not None:
            await self.dispatcher.enqueue(job_id, following)
        # Cancellation/termination leaves a lease + durable pending intent. A
        # real reconciler recovers expired leases; never rely on this process.

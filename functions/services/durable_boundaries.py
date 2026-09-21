"""Isolated durable service boundaries; no exported Firebase HTTP endpoints."""
import asyncio
from dataclasses import dataclass
from services.durable_execution import (AuthorizedStart, Envelope, Lease, Rejected,
                                        VerifiedService, Worker, identifier)
from services import request_auth


class DurableStartService:
    """Existing token/project policy + async project read; never executes work."""
    def __init__(self, core):
        self.core = core

    async def handle(self, request):
        uid = request_auth.authenticate(request)
        if request.method != 'POST':
            raise request_auth.AccessError(405, 'Method not allowed')
        try:
            data = request.get_json(force=True)
            if not isinstance(data, dict):
                raise ValueError()
            project_id = identifier(data.get('projectId'))
            key = identifier(data.get('idempotencyKey'))
            clarification = data.get('clarificationOutput')
            if not isinstance(clarification, dict):
                raise ValueError()
        except Exception:
            raise request_auth.AccessError(400, 'Invalid request') from None
        project = (await self.core.repo.backend.read(f'projects/{project_id}')).data
        def read_project(collection, identity):
            if collection != 'projects' or identity != project_id:
                raise request_auth.AccessError(400, 'Invalid authorization resource')
            return project
        # Reuse the existing project role and claimed-user checks. Estimate IDs
        # are server-derived by the durable core, not claimed by clarification.
        request_auth.authorize('start', uid, {'projectId': project_id,
            'userId': data.get('userId')}, read_document=read_project)
        # These optional producer/request identity claims cannot override verified context.
        if clarification.get('projectId', project_id) != project_id:
            raise request_auth.AccessError(400, 'Clarification project mismatch')
        if any(clarification.get(name, uid) != uid for name in ('userId', 'ownerUid')):
            raise request_auth.AccessError(403, 'Clarification identity mismatch')
        if data.get('estimateId', clarification.get('estimateId')) != clarification.get('estimateId'):
            raise request_auth.AccessError(400, 'Clarification estimate mismatch')
        accepted = await self.core.start(AuthorizedStart(uid, project_id), key, clarification)
        return {**accepted, 'status': 'accepted'}


class IntentScanner:
    """One bounded page, no daemon and no publication lease to abandon.

    Concurrent publishers use deterministic task names. A production dispatcher
    must treat AlreadyExists as accepted. A failed mark leaves a pending intent.
    """
    def __init__(self, core, dispatcher, *, budget_seconds=20):
        if not 0 < budget_seconds <= 60:
            raise ValueError('Invalid scanner budget')
        self.core, self.dispatcher, self.budget = core, dispatcher, budget_seconds

    async def run_page(self, *, after=None, limit=25):
        result = {'visited': 0, 'published': 0, 'errors': 0, 'cursor': after, 'timedOut': False}
        try:
            async with asyncio.timeout(self.budget):
                ids, following = await self.core.repo.backend.scan_jobs(limit=limit, after=after)
                for job_id in ids:
                    try:
                        await self.core.recover(job_id)
                        job = await self.core.repo.get(job_id)
                        if (job and job['status'] not in {'final', 'failed'}
                                and job['leaseExpiresAt'] <= self.core.clock()
                                and job['dispatchIntent']['status'] == 'pending'
                                and job['dispatchIntent']['notBefore'] <= self.core.clock()):
                            published = await self.core.publish(job_id, self.dispatcher)
                            result['published'] += int(bool(published))
                    except Exception:
                        result['errors'] += 1  # Never serialize arbitrary exception text.
                    result['visited'] += 1
                    result['cursor'] = job_id
                result['cursor'] = following
        except TimeoutError:
            result['timedOut'] = True
        return result


@dataclass(frozen=True)
class DurableWriteToken:
    """Identifiers only, forwarded by a verified service adapter, not a browser."""
    job_id: str
    revision: int
    operation_id: str
    generation: int
    attempt: int
    lease_owner: str
    cost_attempt: str | None

    @classmethod
    def from_lease(cls, lease):
        return cls(lease.envelope.job_id, lease.envelope.revision,
                   lease.envelope.operation_id, lease.generation, lease.attempt,
                   lease.owner, lease.cost_attempt)

    def lease(self):
        for number in (self.revision, self.generation, self.attempt):
            if type(number) is not int or number < 0:
                raise Rejected('Invalid durable write token')
        return Lease(Envelope(identifier(self.job_id), self.revision, identifier(self.operation_id)),
                     self.generation, identifier(self.lease_owner), self.cost_attempt, self.attempt)


class DurableOutputBoundary:
    """Only complete the current operation; never accept a caller-chosen path.

    Primary output becomes a candidate; only successful scoring promotes it.
    The real-agent executor stages through the bound FirestoreService; unbound
    legacy writes to durable estimates are rejected by that shared service.
    """
    def __init__(self, core, *, allowed_principal, audience):
        self.core = core
        self.identity = Worker(core, allowed_principal=allowed_principal, audience=audience)

    async def commit(self, identity, token: DurableWriteToken, output):
        if (not isinstance(identity, VerifiedService)
                or identity.principal != self.identity.principal
                or identity.audience != self.identity.audience):
            raise Rejected('Worker identity rejected')
        if not isinstance(token, DurableWriteToken):
            raise Rejected('Durable write token required')
        await self.core.complete(token.lease(), output)

"""Durable state machine core; deliberately not wired to live HTTP/A2A paths.

One operation is one primary/scorer/critic call. Checkpoints and an embedded
outbox intent share a CAS transaction. Only verified boundary adapters may call
start/worker; no body-supplied identity or output is trusted by the worker.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
from typing import Protocol

from agents.agent_cards import AGENT_SEQUENCE
from config.errors import StructuredError, ErrorCode, A2AError, TrueCostError
from services.durable_store import JobRepository, Mutation, Write

TERMINAL = {'final', 'failed'}
ROOT_TERMINAL = TERMINAL | {'completed', 'complete', 'error', 'cancelled'}
MONEY = ('baseEstimate', 'contingency', 'finalEstimate', 'totalCost', 'p50', 'p80', 'p90')


class Rejected(Exception):
    """Fixed internal lifecycle rejection; never contains request/provider text."""


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise Rejected('Invalid identifier')
    return value


@dataclass(frozen=True)
class AuthorizedStart:
    # Created by Firebase user/project authorization adapter, never request JSON.
    owner_uid: str
    project_id: str


@dataclass(frozen=True)
class VerifiedService:
    # Created only after platform OIDC/IAM verification, injected in offline tests.
    principal: str
    audience: str


@dataclass(frozen=True)
class Envelope:
    job_id: str
    revision: int
    operation_id: str
    generation: int | None = None

    @classmethod
    def parse(cls, body):
        if (not isinstance(body, dict) or set(body) not in ({'jobId', 'revision', 'operationId'}, {'jobId', 'revision', 'operationId', 'generation'})
                or type(body['revision']) is not int or body['revision'] < 0):
            raise Rejected('Invalid worker envelope')
        if 'generation' in body and (type(body['generation']) is not int or body['generation'] < 0):
            raise Rejected('Invalid worker generation')
        return cls(identifier(body['jobId']), body['revision'], identifier(body['operationId']), body.get('generation'))

    def to_dict(self):
        return {'jobId': self.job_id, 'revision': self.revision, 'operationId': self.operation_id,
                **({'generation': self.generation} if self.generation is not None else {})}


@dataclass(frozen=True)
class Lease:
    envelope: Envelope
    generation: int
    owner: str
    cost_attempt: str | None
    attempt: int


class Dispatcher(Protocol):
    async def publish(self, *, task_id: str, payload: dict, not_before: float) -> None:
        """Persist task before returning. No identity/outputs in payload."""
        ...


class MissingDispatcher:
    async def publish(self, **kwargs):
        raise Rejected('Production dispatcher is not configured')


class DurableCore:
    def __init__(self, repository: JobRepository, *, clock=time.time,
                 lease_seconds=360, operation_seconds=330, max_deliveries=3,
                 max_quality_retries=2, production=False, dispatcher=None):
        if production and not getattr(dispatcher, "production_ready", False):
            raise Rejected("Real production dispatcher is required")
        if not 300 < operation_seconds < lease_seconds < 540:
            raise ValueError('Worker bounds must surround the unchanged A2A budget')
        if type(max_deliveries) is not int or max_deliveries < 1:
            raise ValueError('Invalid delivery limit')
        if type(max_quality_retries) is not int or max_quality_retries < 0:
            raise ValueError('Invalid quality limit')
        self.repo, self.clock = repository, clock
        self.lease_seconds, self.operation_seconds = lease_seconds, operation_seconds
        self.max_deliveries, self.max_quality_retries = max_deliveries, max_quality_retries

    @staticmethod
    def _intent(job, not_before):
        operation_id = f"{job['jobId']}-r{job['revision']}"
        return {'operationId': operation_id, 'revision': job['revision'],
                'status': 'pending', 'notBefore': not_before, 'publication': 0, 'publishedAt': None}

    async def start(self, authorized: AuthorizedStart, key: str, clarification: dict):
        if not isinstance(authorized, AuthorizedStart):
            raise Rejected('Authorized start context required')
        identifier(authorized.owner_uid)
        identifier(authorized.project_id)
        identifier(key)
        digest = hashlib.sha256(key.encode()).hexdigest()
        job_id, estimate_id = f'job-{digest}', f'est-{digest}'
        fingerprint = hashlib.sha256(json.dumps(clarification, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        now = self.clock()
        response = {'httpStatus': 202, 'jobId': job_id, 'estimateId': estimate_id}
        def change(job):
            if job is not None:
                if (job['ownerUid'], job['projectId'], job['inputFingerprint']) != (
                        authorized.owner_uid, authorized.project_id, fingerprint):
                    raise Rejected('Idempotency conflict')
                return Mutation(job, response)
            job = dict(jobId=job_id, estimateId=estimate_id, projectId=authorized.project_id,
                ownerUid=authorized.owner_uid, inputFingerprint=fingerprint, schemaVersion=1,
                status='queued', currentStage=AGENT_SEQUENCE[0], currentOperation='primary',
                generation=0, revision=0, attempt=0, qualityAttempt=0, providerRetries=0,
                leaseOwner=None, leaseExpiresAt=0, createdAt=now, updatedAt=now,
                lastError=None, checkpoints={}, candidateRef=None, criticRef=None,
                score=None, scoreRef=None, progress=0, costAttempt=None)
            job['dispatchIntent'] = self._intent(job, now)
            return Mutation(job, response, [Write(f'estimates/{estimate_id}',
                {'userId': authorized.owner_uid, 'projectId': authorized.project_id,
                 'status': 'processing', 'clarificationOutput': deepcopy(clarification),
                 'durableJobId': job_id}, create=True)])
        return await self.repo.transact(job_id, change)

    def _terminal(self, job, error):
        job.update(status='failed', lastError=error, leaseOwner=None, leaseExpiresAt=0,
                   updatedAt=self.clock())
        job['dispatchIntent']['status'] = 'cancelled'
        if job['costAttempt']:
            job['costAttempt']['active'] = False
        return Mutation(job, writes=[Write(f"estimates/{job['estimateId']}",
            {'status': 'failed', 'executionError': error,
             **({'costAttempt': job['costAttempt']} if job['costAttempt'] else {})}),
            Write(f"projects/{job['projectId']}/pipeline/status",
                  {'status': 'error', 'estimateId': job['estimateId'], 'executionError': error})])

    async def claim(self, envelope: Envelope, owner: str):
        identifier(owner)
        def change(job):
            now = self.clock()
            if (job is None or job['status'] in TERMINAL or job['revision'] != envelope.revision
                    or job['dispatchIntent']['operationId'] != envelope.operation_id
                    or (envelope.generation is not None and envelope.generation != job['generation'])
                    or job['dispatchIntent']['notBefore'] > now or job['leaseExpiresAt'] > now):
                return None
            if job['attempt'] >= self.max_deliveries:
                return self._terminal(job, {'code': 'PIPELINE_TIMEOUT'})
            job.update(status='running', generation=job['generation'] + 1,
                attempt=job['attempt'] + 1, leaseOwner=owner,
                leaseExpiresAt=now + self.lease_seconds, updatedAt=now, stagedWriteDigests={}, stagedCostItemRefs=[], stagedLedgerDigests={})
            cost_id = None
            if job['currentStage'] == 'cost' and job['currentOperation'] == 'primary':
                cost_id = f"{envelope.operation_id}-g{job['generation']}"
                job['costAttempt'] = {'id': cost_id, 'active': True, 'expiresAt': now + 240}
            return Mutation(job, Lease(envelope, job['generation'], owner, cost_id, job['attempt']),
                [Write(f"estimates/{job['estimateId']}", {'costAttempt': job['costAttempt']})] if cost_id else [])
        return await self.repo.transact(envelope.job_id, change)

    def _fence(self, job, lease, *, check_cost=True):
        if (job is None or job['status'] != 'running'
                or job['generation'] != lease.generation or job['attempt'] != lease.attempt
                or job['revision'] != lease.envelope.revision
                or job['dispatchIntent']['operationId'] != lease.envelope.operation_id
                or job['leaseOwner'] != lease.owner or job['leaseExpiresAt'] <= self.clock()):
            raise Rejected('Stale durable worker')
        cost_primary = job['currentStage'] == 'cost' and job['currentOperation'] == 'primary'
        expected_cost = job['costAttempt']['id'] if cost_primary and job['costAttempt'] else None
        if lease.cost_attempt != expected_cost:
            raise Rejected('Cost attempt identity required')
        if check_cost and cost_primary:
            attempt = job['costAttempt']
            if (not attempt or not attempt['active'] or attempt['expiresAt'] <= self.clock()):
                raise Rejected('Stale Cost attempt')

    def _advance(self, job, *, not_before=None):
        job.update(revision=job['revision'] + 1, attempt=0, leaseOwner=None,
                   leaseExpiresAt=0, status='queued', updatedAt=self.clock())
        job['dispatchIntent'] = self._intent(job, self.clock() if not_before is None else not_before)

    async def revoke_cost(self, lease):
        def change(job):
            self._fence(job, lease)
            if lease.cost_attempt is None:
                raise Rejected('Cost lease required')
            job['costAttempt']['active'] = False
            return Mutation(job, writes=[Write(f"estimates/{job['estimateId']}",
                {'costAttempt': job['costAttempt']})])
        await self.repo.transact(lease.envelope.job_id, change)

    async def complete(self, lease: Lease, output: dict):
        if not isinstance(output, dict):
            return await self.fail(lease, StructuredError(ErrorCode.LLM_INVALID_RESPONSE))
        before = await self.repo.get(lease.envelope.job_id)
        self._fence(before, lease)
        cost_snapshot = None
        if before['currentStage'] == 'cost' and before['currentOperation'] in ('primary', 'scorer'):
            cost_snapshot = await self.repo.backend.read(f"estimates/{before['estimateId']}")
            root = cost_snapshot.data or {}
            attempt = root.get('costAttempt', {})
            if (root.get('status') in ROOT_TERMINAL
                    or root.get('pipelineStatus', {}).get('status') in ROOT_TERMINAL
                    or attempt.get('id') != before['costAttempt']['id']
                    or (lease.cost_attempt and (not attempt.get('active')
                        or attempt.get('expiresAt', 0) <= self.clock()))):
                raise Rejected('Stale Cost attempt')
        from services.durable_agents import staging_path
        staged = (await self.repo.backend.read(staging_path(lease))).data or {}
        if 'output' in staged and staged['output'] != output:
            raise Rejected('Operation result differs from staged output')
        candidate_record = ((await self.repo.backend.read(before['candidateRef'])).data
                            if before['currentOperation'] == 'scorer' else None)
        candidate = candidate_record['output'] if candidate_record else None
        ledger = []
        if candidate_record and before['currentStage'] == 'cost':
            refs = candidate_record.get('costItemRefs', [])
            records = await self.repo.backend.read_many(refs) if refs else {}
            for ref in refs:
                item = records.get(ref)
                if item is None or item.data is None:
                    raise Rejected('Staged ledger missing')
                ledger.append(item.data)
        def change(job):
            self._fence(job, lease)
            stage, operation = job['currentStage'], job['currentOperation']
            path = f"durableJobs/{job['jobId']}/checkpoints/{lease.envelope.operation_id}"
            writes = [Write(path, {'output': deepcopy(output), 'status': 'completed',
                                  'generation': lease.generation,
                                  **{k: staged[k] for k in ('rootPatch','costItemRefs') if k in staged}}, create=True)]
            if operation == 'primary':
                job.update(candidateRef=path, currentOperation='scorer')
                if lease.cost_attempt:
                    job['costAttempt']['active'] = False
                    writes.append(Write(f"estimates/{job['estimateId']}",
                        {'costAttempt': job['costAttempt']}, version=cost_snapshot.version))
            elif operation == 'scorer':
                score, passed = output.get('score'), output.get('passed')
                if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100 or type(passed) is not bool:
                    raise Rejected('Invalid scorer result')
                job['score'] = score
                job['scoreRef'] = path
                if passed:
                    job['checkpoints'][stage] = {'outputRef': job['candidateRef'], 'score': score}
                    job['progress'] = len(job['checkpoints']) / len(AGENT_SEQUENCE) * 100
                    writes.extend([Write(f"estimates/{job['estimateId']}/agentOutputs/{stage}",
                        {'output': candidate, 'status': 'completed', 'score': score}),
                        Write(f"estimates/{job['estimateId']}", {f'{stage}Output': candidate},
                              version=cost_snapshot.version if cost_snapshot else None),
                        Write(f"projects/{job['projectId']}/pipeline/status", {
                            'estimateId': job['estimateId'], 'status': 'running',
                            'completedStages': [s for s in AGENT_SEQUENCE if s in job['checkpoints']], 'progress': job['progress']})])
                    if stage == 'cost':
                        writes.extend(Write(f"estimates/{job['estimateId']}/costItems/{item['ledgerId']}",
                                            item['output']) for item in ledger)
                    if stage == 'final':
                        # Final adapter supplies authoritative root fields; no arithmetic here.
                        root_patch = candidate_record.get('rootPatch', {})
                        authoritative = root_patch or candidate
                        money = {k: authoritative[k] for k in MONEY if k in authoritative}
                        if any(k not in money for k in ('baseEstimate', 'contingency', 'finalEstimate', 'totalCost')) or any(
                                type(v) not in (int, float) or not math.isfinite(v) for v in money.values()):
                            raise Rejected('Final monetary mapping missing')
                        job.update(status='final', progress=100, leaseOwner=None,
                                   leaseExpiresAt=0, updatedAt=self.clock())
                        job['dispatchIntent']['status'] = 'completed'
                        writes.extend([Write(f"estimates/{job['estimateId']}", {**root_patch, **money, 'status': 'final'}),
                            Write(f"projects/{job['projectId']}/pipeline/status", {'status': 'complete', 'progress': 100})])
                        return Mutation(job, writes=writes)
                    job.update(currentStage=AGENT_SEQUENCE[len(job['checkpoints'])],
                        currentOperation='primary', qualityAttempt=0, providerRetries=0,
                        candidateRef=None, criticRef=None, score=None, scoreRef=None)
                elif job['qualityAttempt'] >= self.max_quality_retries:
                    terminal = self._terminal(job, {'code': 'AGENT_MAX_RETRIES_EXCEEDED'})
                    terminal.writes = writes + terminal.writes
                    return terminal
                else:
                    job['currentOperation'] = 'critic'
            elif operation == 'critic':
                job.update(criticRef=path, currentOperation='primary',
                           qualityAttempt=job['qualityAttempt'] + 1)
            else:
                raise Rejected('Unknown durable operation')
            self._advance(job)
            return Mutation(job, writes=writes)
        await self.repo.transact(lease.envelope.job_id, change)

    async def fail(self, lease, error):
        # Reconstruct rather than serialize possibly mutated exception attributes.
        safe = (StructuredError.from_dict({'code': error.code, 'details': error.details}).to_dict()
                if isinstance(error, StructuredError) else {'code': 'PIPELINE_FAILED'})
        def change(job):
            self._fence(job, lease, check_cost=False)
            job['lastError'] = safe
            delay = safe.get('details', {}).get('retry_delay')
            if (safe['code'] == ErrorCode.LLM_RATE_LIMITED and job['currentOperation'] == 'primary'
                    and job['providerRetries'] == 0 and delay is not None and 0 <= delay <= 15):
                job['providerRetries'] += 1
                if job['costAttempt']:
                    job['costAttempt']['active'] = False
                self._advance(job, not_before=self.clock() + max(1.0, delay))
                return Mutation(job, writes=[Write(f"estimates/{job['estimateId']}",
                    {'costAttempt': job['costAttempt']})] if job['costAttempt'] else [])
            return self._terminal(job, safe)
        await self.repo.transact(lease.envelope.job_id, change)

    async def execution_failure(self, lease, error):
        """Preserve existing unstructured A2A scorer/critic fallback semantics."""
        if isinstance(error, StructuredError) or not isinstance(error, (A2AError, TrueCostError)):
            return await self.fail(lease, error)
        job = await self.repo.get(lease.envelope.job_id)
        self._fence(job, lease, check_cost=False)
        if job['currentOperation'] == 'scorer':
            return await self.complete(lease, {'score': 80, 'passed': True,
                'breakdown': [], 'feedback': 'Scorer unavailable, defaulting to pass'})
        if job['currentOperation'] == 'critic':
            return await self.complete(lease, {
                'issues': [f"Score was {job['score']}/100, below threshold"],
                'why_wrong': 'Output did not meet quality standards',
                'how_to_fix': ['Review and improve the output quality'], 'score': job['score']})
        def change(current):
            self._fence(current, lease, check_cost=False)
            if current['qualityAttempt'] >= self.max_quality_retries:
                return self._terminal(current, {'code': 'AGENT_MAX_RETRIES_EXCEEDED'})
            current['qualityAttempt'] += 1
            if current['costAttempt']:
                current['costAttempt']['active'] = False
            self._advance(current)
            return Mutation(current, writes=[Write(f"estimates/{current['estimateId']}",
                {'costAttempt': current['costAttempt']})] if current['costAttempt'] else [])
        await self.repo.transact(lease.envelope.job_id, change)

    async def recover(self, job_id):
        """Reconciler boundary: re-open expired work's intent, never execute it."""
        def change(job):
            if job is None or job['status'] in TERMINAL or job['leaseExpiresAt'] > self.clock():
                return None
            intent = job['dispatchIntent']
            # A queued task needs a delivery window; do not republish every scan.
            if (job['status'] == 'queued' and intent['status'] == 'dispatched'
                    and intent.get('publishedAt') is not None
                    and intent['publishedAt'] + self.lease_seconds > self.clock()):
                return None
            if job['attempt'] >= self.max_deliveries or intent['publication'] >= self.max_deliveries:
                return self._terminal(job, {'code': 'PIPELINE_TIMEOUT'})
            job.update(status='queued', leaseOwner=None, leaseExpiresAt=0)
            if job['dispatchIntent']['status'] != 'pending':
                job['dispatchIntent']['publication'] += 1
                job['dispatchIntent']['status'] = 'pending'
            return Mutation(job)
        await self.repo.transact(job_id, change)

    async def publish(self, job_id, dispatcher: Dispatcher | None = None):
        job = await self.repo.get(job_id)
        if job is None or job['status'] in TERMINAL or job['dispatchIntent']['status'] != 'pending':
            return
        intent = job['dispatchIntent']
        envelope = Envelope(job_id, job['revision'], intent['operationId'], job['generation'])
        await (dispatcher if dispatcher is not None else MissingDispatcher()).publish(
            task_id=f"{intent['operationId']}-p{intent['publication']}", payload=envelope.to_dict(), not_before=intent['notBefore'])
        def change(current):
            if (current['revision'] != envelope.revision or current['status'] in TERMINAL
                    or current['dispatchIntent']['publication'] != intent['publication']):
                return None
            current['dispatchIntent']['status'] = 'dispatched'
            current['dispatchIntent']['publishedAt'] = self.clock()
            return Mutation(current)
        await self.repo.transact(job_id, change)
        return True


class Worker:
    def __init__(self, core, *, allowed_principal: str, audience: str):
        if not allowed_principal or not audience:
            raise Rejected('Worker identity configuration required')
        self.core, self.principal, self.audience = core, allowed_principal, audience

    async def handle(self, identity: VerifiedService, body, *, lease_owner, execute):
        if (not isinstance(identity, VerifiedService) or identity.principal != self.principal
                or identity.audience != self.audience):
            raise Rejected('Worker identity rejected')
        envelope = Envelope.parse(body)
        lease = await self.core.claim(envelope, lease_owner)
        if lease is None:
            return
        job = await self.core.repo.get(envelope.job_id)
        try:
            async with asyncio.timeout(self.core.operation_seconds):
                output = await execute(deepcopy(job), lease)
        except StructuredError as error:
            await self.core.fail(lease, error)
        except TimeoutError:
            await self.core.execution_failure(lease, A2AError(
                ErrorCode.A2A_TIMEOUT, 'Bounded worker operation timed out', target_agent=job['currentStage']))
        except Exception as error:
            await self.core.execution_failure(lease, error)
        else:
            await self.core.complete(lease, output)

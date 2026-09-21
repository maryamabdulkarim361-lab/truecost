"""Real-agent persistence binding. All agent writes are fenced staging writes.

Only DurableCore promotes accepted output. Model data never selects a token,
job, revision, write path or authorization decision.
"""
from copy import deepcopy
import hashlib
import json
from services.durable_execution import Rejected, AGENT_SEQUENCE
from services.durable_store import Mutation, Write
from config.errors import StructuredError, A2AError, ErrorCode


def staging_path(lease):
    return (f'durableJobs/{lease.envelope.job_id}/attemptWrites/'
            f'{lease.envelope.operation_id}-g{lease.generation}')


class AgentWriteContext:
    def __init__(self, core, lease, job):
        self.core, self.lease = core, lease
        self.estimate_id = job['estimateId']
        self.failed = False
        self.stage, self.operation = job['currentStage'], job['currentOperation']
        self.name = self.stage if self.operation == 'primary' else f'{self.stage}_{self.operation}'

    async def write(self, estimate_id, kind, data, *, agent_name=None, attempt_id=None):
        if self.failed:
            raise Rejected('Agent persistence previously failed')
        if estimate_id != self.estimate_id or (agent_name is not None and agent_name != self.name):
            raise Rejected('Agent write identity mismatch')
        if self.stage == 'cost' and self.operation == 'primary' and attempt_id != self.lease.cost_attempt:
            raise Rejected('Cost attempt identity required')
        if kind == 'rootPatch' and (self.stage, self.operation) != ('final', 'primary'):
            raise Rejected('Only Final can stage root integration')
        if kind == 'rootPatch' and any(k in data for k in (
                'status','progress','pipelineStatus','userId','projectId','durableJobId','costAttempt')):
            raise Rejected('Agent cannot write control state')
        if kind == 'costItems' and (self.stage, self.operation) != ('cost', 'primary'):
            raise Rejected('Only Cost can stage the ledger')
        path = staging_path(self.lease)
        cost_snapshot = None
        if self.lease.cost_attempt:
            cost_snapshot = await self.core.repo.backend.read(f'estimates/{estimate_id}')
            root = cost_snapshot.data or {}
            attempt = root.get('costAttempt', {})
            if (attempt.get('id') != self.lease.cost_attempt or not attempt.get('active')
                    or attempt.get('expiresAt', 0) <= self.core.clock()
                    or root.get('status') in {'final','failed','complete','completed','cancelled','error'}):
                raise Rejected('Stale Cost attempt')
        def change(job):
            self.core._fence(job, self.lease)
            if (job['estimateId'], job['currentStage'], job['currentOperation']) != (
                    self.estimate_id, self.stage, self.operation):
                raise Rejected('Agent operation mismatch')
            digest = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                                allow_nan=False).encode()).hexdigest()
            digests = job.setdefault('stagedWriteDigests', {})
            if kind != 'costItems' and kind in digests and digests[kind] != digest:
                raise Rejected('Conflicting duplicate agent write')
            if kind != 'costItems':
                digests[kind] = digest
            writes = []
            if cost_snapshot:
                writes.append(Write(f'estimates/{estimate_id}', {'costAttempt':job['costAttempt']},
                                    version=cost_snapshot.version))
            if kind == 'costItems':
                refs = job.setdefault('stagedCostItemRefs', [])
                item_digests = job.setdefault('stagedLedgerDigests', {})
                for item in data:
                    if not isinstance(item, dict):
                        continue  # Same existing ledger behavior.
                    item_id = item.get('id') or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
                    if not isinstance(item_id, str) or '/' in item_id or item_id in {'.','..'} or len(item_id.encode()) > 1500:
                        raise Rejected('Invalid ledger identity')
                    safe_id = hashlib.sha256(item_id.encode()).hexdigest()
                    ref = f'{path}/costItems/{safe_id}'
                    item_digest = hashlib.sha256(json.dumps(item, sort_keys=True, allow_nan=False).encode()).hexdigest()
                    if safe_id in item_digests and item_digests[safe_id] != item_digest:
                        raise Rejected('Conflicting duplicate ledger item')
                    item_digests[safe_id] = item_digest
                    if ref not in refs:
                        refs.append(ref)
                    writes.append(Write(ref, {'output': deepcopy(item), 'ledgerId': item_id}))
                if len(refs) > 400:
                    self.failed = True
                    raise Rejected('Ledger exceeds atomic promotion budget')
                writes.append(Write(path, {'costItemRefs': refs}))
            elif kind in {'output', 'rootPatch', 'agentStatus'}:
                writes.append(Write(path, {kind: deepcopy(data)}))
            else:
                raise Rejected('Unsupported durable agent write')
            return Mutation(job, writes=writes)
        await self.core.repo.transact(self.lease.envelope.job_id, change)

    async def list_cost_items(self, estimate_id, limit=None):
        if estimate_id != self.estimate_id:
            raise Rejected('Estimate mismatch')
        query = self.core.repo.backend.client.collection(f'estimates/{estimate_id}/costItems')
        if limit is not None:
            query = query.limit(int(limit))
        return [{'id':s.id, **s.to_dict()} async for s in query.stream(retry=None, timeout=3)]


class DurableAgentExecutor:
    """Run existing A2A agent handlers in the worker loop, with injected services.

    Factory is server configuration (never task/model data). It returns existing
    primary/scorer/critic classes. Tests inject generation/provider fakes. The
    factory owns any non-LLM dependencies; LLM cleanup finishes in this loop.
    """
    def __init__(self, core, agent_factory):
        self.core, self.agent_factory = core, agent_factory

    async def __call__(self, job, lease):
        from services.firestore_service import FirestoreService
        # Reload; do not trust an executor caller's stage or identity snapshot.
        job = await self.core.repo.get(lease.envelope.job_id)
        self.core._fence(job, lease)
        context = AgentWriteContext(self.core, lease, job)
        storage = FirestoreService(db=self.core.repo.backend.client, durable_context=context)
        root = (await self.core.repo.backend.read(f"estimates/{job['estimateId']}")).data
        inputs = {'clarification_output': root['clarificationOutput']}
        for name in AGENT_SEQUENCE:
            if name in job['checkpoints']:
                inputs[f'{name}_output'] = await self.core.repo.output(job['checkpoints'][name]['outputRef'])
        message = {'estimate_id':job['estimateId'], 'input':inputs, 'retry_attempt':job['qualityAttempt']}
        if lease.cost_attempt:
            message.update(attempt_id=lease.cost_attempt, attempt_expires_at=job['costAttempt']['expiresAt'])
        if job['currentOperation'] == 'primary' and job['criticRef']:
            message['critic_feedback'] = await self.core.repo.output(job['criticRef'])
        if job['currentOperation'] != 'primary':
            message.update(agent_name=job['currentStage'],
                           output=await self.core.repo.output(job['candidateRef']))
        if job['currentOperation'] == 'critic':
            score = await self.core.repo.output(job['scoreRef'])
            message.update(score=job['score'], scorer_feedback=score.get('feedback',''))
        agent = self.agent_factory(context.name, storage)
        if agent.firestore is not storage or agent.name != context.name:
            raise Rejected('Agent factory binding mismatch')
        async with agent.llm:
            response = await agent.handle_a2a_request({'jsonrpc':'2.0',
                'id':lease.envelope.operation_id, 'method':'message/send',
                'params':{'message':{'parts':[{'type':'data','data':message}]}}})
        result = response.get('result', {})
        if result.get('status') != 'completed':
            error = result.get('error')
            if isinstance(error, dict) and error.get('code') in StructuredError.MESSAGES:
                raise StructuredError.from_dict(error)
            raise A2AError(ErrorCode.AGENT_FAILED, 'Durable agent execution failed', target_agent=context.name)
        output = result.get('result')
        if not isinstance(output, dict):
            raise StructuredError(ErrorCode.LLM_INVALID_RESPONSE)
        # Also captures scorer/critic results, which normally only return via A2A.
        await storage.save_agent_output(job['estimateId'], context.name, output,
                                        attempt_id=lease.cost_attempt)
        if (context.stage, context.operation) == ('final', 'primary'):
            staged = (await self.core.repo.backend.read(staging_path(lease))).data or {}
            if not staged.get('rootPatch'):
                raise Rejected('Final authoritative mapping was not staged')
        return output


def real_agent(name, storage, llm, **dependencies):
    """Explicit server factory for existing classes, never a model-selected type."""
    import importlib
    operation = 'primary'
    stage = name
    for suffix in ('_scorer','_critic'):
        if name.endswith(suffix):
            stage, operation = name[:-len(suffix)], suffix[1:]
    if stage not in AGENT_SEQUENCE:
        raise Rejected('Unknown agent')
    package = 'primary' if operation == 'primary' else operation + 's'
    ending = 'agent' if operation == 'primary' else operation
    cls_name = ''.join(part.title() for part in stage.split('_')) + ending.title()
    cls = getattr(importlib.import_module(f'agents.{package}.{stage}_{ending}'), cls_name)
    return cls(firestore_service=storage, llm_service=llm, **dependencies)

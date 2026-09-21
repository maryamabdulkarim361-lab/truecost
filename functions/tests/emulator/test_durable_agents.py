"""Real A2A handlers + FirestoreService boundaries; no provider execution.

Primary business results except Final are fixture replacements. Final runs its
existing implementation with a mocked generation result. No agent algorithms
are being live-validated here; this suite validates persistence and routing.
"""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
from services.durable_agents import AgentWriteContext, DurableAgentExecutor, real_agent, staging_path
from services.firestore_service import FirestoreService
from services.durable_dispatch import DurableWorkerService
from services.durable_execution import VerifiedService, Rejected, AGENT_SEQUENCE
from services.durable_boundaries import IntentScanner
from tests.emulator.test_durable_firestore import env, FakeDispatcher, success_stage
from tests.unit.test_final_offline import inputs


def offline_agent(name, storage, llm):
    dependencies = {}
    if name in ('location','scope','cost'):
        dependencies['cost_data_service'] = object()
    if name in ('location','cost','code_compliance'):
        dependencies['serper_service'] = object()
    if name == 'cost':
        dependencies['labor_productivity_service'] = object()
    return real_agent(name, storage, llm, **dependencies)


class FakeLLM:
    total_tokens_used = 0
    def __init__(self):
        self.closed = False
        self.generate_json = AsyncMock(return_value={'content':{
            'recommendations':[], 'key_assumptions':['offline fixture'], 'exclusions':[]}, 'tokens_used':0})
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True


async def at_stage(env, stage):
    for _ in range(AGENT_SEQUENCE.index(stage)):
        await success_stage(env)


async def binding(env):
    lease = await env.claim()
    job = await env.job()
    context = AgentWriteContext(env.core, lease, job)
    return lease, FirestoreService(db=env.client, durable_context=context)


@pytest.mark.parametrize('stage', AGENT_SEQUENCE)
@pytest.mark.asyncio
async def test_each_real_service_boundary_fences_primary(env, stage):
    await at_stage(env, stage)
    lease, storage = await binding(env)
    estimate = (await env.job())['estimateId']
    llm = FakeLLM(); agent = offline_agent(stage, storage, llm)
    assert agent.firestore is storage
    output = ({'baseEstimate':27942.71,'contingency':3514.03,'finalEstimate':31456.74,
               'totalCost':31456.74,'p50':29283.60,'p80':32797.63,'p90':35067.77}
              if stage == 'final' else {'fixture':stage})
    await storage.save_agent_output(estimate, agent.name, output, attempt_id=lease.cost_attempt)
    root = (await env.client.document(f'estimates/{estimate}').get()).to_dict()
    assert f'{stage}Output' not in root  # Not accepted before scoring.
    for invalid in (replace(lease, generation=999), replace(lease, attempt=999),
                    replace(lease, envelope=replace(lease.envelope, revision=999))):
        other = FirestoreService(db=env.client, durable_context=AgentWriteContext(env.core, invalid, await env.job()))
        with pytest.raises(Rejected):
            await other.save_agent_output(estimate, stage, {'stale':True}, attempt_id=invalid.cost_attempt)
    await env.core.complete(lease, output)
    await env.core.complete(await env.claim(), {'score':95,'passed':True})
    accepted = await env.client.document(f'estimates/{estimate}/agentOutputs/{stage}').get()
    assert accepted.to_dict()['output'] == output
    with pytest.raises(Rejected):
        await storage.save_agent_output(estimate, stage, {'late':True}, attempt_id=lease.cost_attempt)
    assert await env.core.claim(lease.envelope, 'duplicate') is None


@pytest.mark.parametrize('operation', ['scorer','critic'])
@pytest.mark.parametrize('stage', AGENT_SEQUENCE)
@pytest.mark.asyncio
async def test_real_scorer_critic_result_boundaries(env, stage, operation):
    await at_stage(env, stage)
    await env.core.complete(await env.claim(), {'fixture':stage})
    if operation == 'critic':
        await env.core.complete(await env.claim(), {'score':35,'passed':False,'feedback':'fixture'})
    lease, storage = await binding(env)
    agent = offline_agent(f'{stage}_{operation}', storage, FakeLLM())
    estimate = (await env.job())['estimateId']
    output = {'score':35,'passed':False} if operation == 'scorer' else {'feedback':'fixture'}
    await storage.save_agent_output(estimate, agent.name, output)
    await env.core.complete(lease, output)
    with pytest.raises(Rejected):
        await storage.save_agent_output(estimate, agent.name, {'late':True})
    assert await env.core.claim(lease.envelope, 'duplicate') is None


@pytest.mark.asyncio
async def test_unbound_legacy_writes_cannot_touch_durable_estimate(env):
    storage = FirestoreService(db=env.client)
    estimate = (await env.job())['estimateId']
    operations = [lambda:storage.update_estimate(estimate, {'totalCost':1}),
        lambda:storage.update_agent_status(estimate,'location','completed'),
        lambda:storage.save_agent_output(estimate,'location',{'bad':True}),
        lambda:storage.save_cost_items(estimate,[{'id':'bad'}]),
        lambda:storage.begin_cost_attempt(estimate,'old',9999),
        lambda:storage.delete_estimate(estimate),
        lambda:storage.sync_to_project_pipeline('project',estimate,None,[],100,'complete')]
    for call in operations:
        with pytest.raises(Rejected):
            await call()
    assert 'totalCost' not in (await env.client.document(f'estimates/{estimate}').get()).to_dict()


@pytest.mark.asyncio
async def test_old_cost_outer_token_valid_old_inner_attempt_is_rejected(env):
    await at_stage(env, 'cost')
    old, old_storage = await binding(env)
    estimate = (await env.job())['estimateId']
    env.clock.now += 361
    new, storage = await binding(env)
    # Even restore the old inner token to apparently valid state: outer fence wins.
    await env.client.document(f'estimates/{estimate}').update({'costAttempt':{
        'id':old.cost_attempt,'active':True,'expiresAt':env.clock.now+1000}})
    with pytest.raises(Rejected):
        await old_storage.save_agent_output(estimate,'cost',{},attempt_id=old.cost_attempt)
    with pytest.raises(Rejected):
        await storage.save_agent_output(estimate,'cost',{},attempt_id=new.cost_attempt)
    await env.client.document(f'estimates/{estimate}').update({'costAttempt':(await env.job())['costAttempt']})
    with pytest.raises(Rejected):
        await storage.save_agent_output(estimate,'cost',{},attempt_id=None)
    await storage.save_agent_output(estimate,'cost',{'new':True},attempt_id=new.cost_attempt)


@pytest.mark.asyncio
async def test_real_boundary_e2e_with_real_final_mapping(env, inputs):
    estimate = (await env.job())['estimateId']
    await env.client.document(f'estimates/{estimate}').update({'clarificationOutput':inputs['clarification_output']})
    instances = []; calls = []; saved_final = []
    def factory(name, storage):
        llm = FakeLLM(); agent = offline_agent(name, storage, llm)
        instances.append(agent)
        stage = storage._durable.stage; operation = storage._durable.operation
        if operation == 'primary' and stage != 'final':
            async def run(estimate_id, input_data, feedback=None):
                calls.append((stage,'primary'))
                result = deepcopy(inputs[f'{stage}_output'])
                if stage == 'cost':
                    await storage.save_cost_items(estimate_id,[{'id':'fixture-ledger','totalCost':100}],
                                                  attempt_id=agent._attempt_id)
                    await storage.save_cost_items(estimate_id,[{'id':'second-division-ledger','totalCost':200}],
                                                  attempt_id=agent._attempt_id)
                await storage.save_agent_output(estimate_id,stage,result,attempt_id=getattr(agent,'_attempt_id',None))
                return result
            agent.run = run
        elif operation == 'scorer':
            async def score(estimate_id, output, input_data):
                calls.append((stage,'scorer'))
                job = await env.job()
                passed = not(stage == 'scope' and job['qualityAttempt'] == 0)
                return {'score':95 if passed else 35,'passed':passed,'feedback':'fixture'}
            agent.score = score
        elif operation == 'critic':
            async def critique(**kwargs):
                calls.append((stage,'critic'))
                assert kwargs['score'] == 35 and kwargs['scorer_feedback'] == 'fixture'
                return {'feedback':'fixture improvement'}
            agent.critique = critique
        else:
            saved_final.append(storage)
        return agent
    executor = DurableAgentExecutor(env.core, factory)
    service = DurableWorkerService(env.core, principal='sa', audience='worker', execute=executor)
    identity = VerifiedService('sa','worker')
    dispatch = FakeDispatcher(); scanner = IntentScanner(env.core, dispatch)
    for _ in range(30):
        job = await env.job()
        if job['status'] == 'final':
            break
        assert (await scanner.run_page())['errors'] == 0
        payload = dispatch.calls[-1]['payload']
        await service.handle(identity,payload,lease_owner='worker')
        assert (await service.handle(identity,payload,lease_owner='duplicate'))['status'] == 'rejected'
    job = await env.job()
    assert job['status'] == 'final', job.get('lastError')
    root = (await env.client.document(f'estimates/{estimate}').get()).to_dict()
    assert root['finalEstimate'] == root['totalCost'] == 31456.74
    assert [root[k] for k in ('p50','p80','p90')] == [29283.60,32797.63,35067.77]
    assert root['costItemsCount'] == 2 and root['status'] == 'final'
    assert (await env.client.document(f'estimates/{estimate}/costItems/fixture-ledger').get()).exists
    assert (await env.client.document(f'estimates/{estimate}/costItems/second-division-ledger').get()).exists
    assert job['progress'] == 100 and len(instances) == 17
    assert all(agent.llm.closed for agent in instances)
    assert ('scope','critic') in calls
    previous = deepcopy(root)
    # The original Final context may not rewrite money/status after finalization.
    with pytest.raises(Rejected):
        await saved_final[0].update_estimate(estimate,{'finalEstimate':1,'totalCost':1,'p80':1})
    with pytest.raises(Rejected):
        await saved_final[0].update_agent_status(estimate,'final','running')
    assert (await env.client.document(f'estimates/{estimate}').get()).to_dict() == previous

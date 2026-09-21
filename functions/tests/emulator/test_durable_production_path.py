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


from services.durable_http import handle, verify_service
from services.cloud_tasks_dispatcher import CloudTasksDispatcher
from services.durable_dispatch import TaskConfiguration
from flask import Request
from werkzeug.test import EnvironBuilder
from pathlib import Path
import json
import base64
from types import SimpleNamespace


def request(body):
    return Request(EnvironBuilder(method='POST',json=body,headers={'Authorization':'Bearer fake'}).get_environ())

@pytest.mark.asyncio
async def test_production_http_outbox_e2e(env, inputs, monkeypatch):
    worker='https://worker.example.com/durable_worker'; scanner_url='https://scanner.example.com/durable_scanner'
    principal='worker@offline-project.iam.gserviceaccount.com'
    for kind,url in (('WORKER',worker),('SCANNER',scanner_url)):
        monkeypatch.setenv(f'DURABLE_{kind}_URL',url)
        monkeypatch.setenv(f'DURABLE_{kind}_INVOKER',principal)
    monkeypatch.setattr('services.durable_boundaries.request_auth.authenticate',lambda r:'owner')
    await env.client.document('projects/project').set({'ownerId':'owner'})
    clarification=json.loads(Path('functions/tests/fixtures/clarification_output_kitchen.json').read_text())
    body={'projectId':'project','idempotencyKey':'public-test','clarificationOutput':clarification}
    accepted,status=await handle('start',request(body),env.core)
    assert status==202
    again,status=await handle('start',request(body),env.core)
    assert status==202 and again==accepted
    env.start.update(accepted['data'])
    estimate=env.start['estimateId']
    assert (await env.job())['dispatchIntent']['status']=='pending'
    payloads=[]
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,**kwargs):
            payloads.append(json.loads(base64.b64decode(kwargs['json']['task']['httpRequest']['body'])))
            return SimpleNamespace(status_code=200)
    dispatch=CloudTasksDispatcher(TaskConfiguration('projects/offline-project/locations/us-central1/queues/durable',worker,principal),Session)
    def identity(url):
        return verify_service(request({}),principal,url,lambda *a:{'iss':'https://accounts.google.com',
            'aud':url,'email':principal,'email_verified':True,'sub':'123'})
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
    for _ in range(30):
        job = await env.job()
        if job['status'] == 'final':
            break
        page,status=await handle('scanner',request({}),env.core,dispatcher=dispatch,identity=identity(scanner_url))
        assert status==200 and page['errors']==0
        payload=payloads[-1]
        result,status=await handle('worker',request(payload),env.core,execute=executor,identity=identity(worker))
        assert status==200
        duplicate,status=await handle('worker',request(payload),env.core,execute=executor,identity=identity(worker))
        assert duplicate['status']=='rejected'
    job = await env.job()
    assert job['status'] == 'final', job.get('lastError')
    root = (await env.client.document(f'estimates/{estimate}').get()).to_dict()
    assert root['finalEstimate'] == root['totalCost'] == 31456.74
    assert root['baseEstimate']==27942.71 and root['contingency']==3514.03
    assert root['pipelineStatus']['progress']==100
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

@pytest.mark.asyncio
async def test_rest_enqueue_failure_keeps_intent_recoverable(env):
    responses=iter([503,409])
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,*args,**kwargs): return SimpleNamespace(status_code=next(responses))
    dispatcher=CloudTasksDispatcher(TaskConfiguration('projects/offline-project/locations/us-central1/queues/durable',
        'https://worker.example.com/worker','worker@offline-project.iam.gserviceaccount.com'),Session)
    scanner=IntentScanner(env.core,dispatcher)
    result=await scanner.run_page()
    assert result['errors']==1 and result['published']==0
    assert (await env.job())['dispatchIntent']['status']=='pending'
    result=await scanner.run_page()
    assert result['errors']==0 and result['published']==1
    assert (await env.job())['dispatchIntent']['status']=='dispatched'

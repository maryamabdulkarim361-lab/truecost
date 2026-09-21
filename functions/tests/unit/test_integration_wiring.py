"""Real orchestration/A2A/persistence with in-memory HTTP and Firestore boundaries."""
import copy
import json
from pathlib import Path
import httpx
import pytest
from agents.orchestrator import PipelineOrchestrator
from agents.agent_cards import AGENT_SEQUENCE
from services.a2a_client import A2AClient
from services.firestore_service import FirestoreService
from tests.unit.test_final_offline import inputs, agent
from tests.unit.test_cost_execution_safety import FakeDB, Ref, apply_fields

pytestmark=pytest.mark.usefixtures('block_network')

@pytest.mark.asyncio
async def test_seven_stage_wiring(monkeypatch, inputs, agent):
    db=FakeDB(); history=[]
    def update(ref,data):
        apply_fields(db.docs.setdefault(ref.path,{}),copy.deepcopy(data));db.version+=1
        history.append((ref.path,copy.deepcopy(data)))
    def set_doc(ref,data,merge=False):
        if not merge: db.docs[ref.path]={}
        update(ref,data)
    monkeypatch.setattr(Ref,'update',update,raising=False)
    monkeypatch.setattr(Ref,'set',set_doc,raising=False)
    monkeypatch.setattr(Ref,'order_by',lambda self,*a,**k:self,raising=False)
    monkeypatch.setattr(Ref,'limit',lambda self,*a,**k:self,raising=False)
    monkeypatch.setattr(Ref,'stream',lambda self:iter([]),raising=False)
    store=FirestoreService(db=db); agent.firestore=store
    order=[]; urls=[]
    async def transport(request):
        urls.append(str(request.url)); name=request.url.path.split('/a2a_')[1]
        payload=json.loads(request.content)
        message=payload['params']['message']['parts'][0]['data']
        if name.endswith('_scorer'):
            output={'score':95,'passed':True,'breakdown':[],'feedback':'offline fixture'}
        else:
            assert name in AGENT_SEQUENCE  # No critic or unapproved endpoint.
            assert list(message['input'])==['clarification_output']+[f'{n}_output' for n in order]
            order.append(name)
            output=await agent.run('e',message['input']) if name=='final' else inputs[f'{name}_output']
        return httpx.Response(200,json={'jsonrpc':'2.0','id':payload['id'],'result':{'status':'completed','result':output}})
    real_client=httpx.AsyncClient
    monkeypatch.setattr('services.a2a_client.httpx.AsyncClient',lambda:real_client(transport=httpx.MockTransport(transport)))
    result=await PipelineOrchestrator(store,A2AClient(base_url='http://127.0.0.1:5003/collabcanvas-dev/us-central1')).run_pipeline('e',inputs['clarification_output'],'project','user')
    assert result.success and order==AGENT_SEQUENCE
    assert len(urls)==14 and all(':5003/' in u for u in urls)
    root=db.docs['estimates/e']
    assert root['status']=='final' and root['pipelineStatus']['progress']==100
    assert root['pipelineStatus']['completedAgents']==AGENT_SEQUENCE
    for name in AGENT_SEQUENCE:
        assert db.docs[f'estimates/e/agentOutputs/{name}']['status']=='completed'
        assert root[f'{name}Output']
    project=db.docs['projects/project/pipeline/status']
    assert project['status']=='complete' and project['completedStages']==AGENT_SEQUENCE
    expected=json.loads((Path(__file__).parents[1]/'fixtures/authoritative_cost_contract.json').read_text())
    assert {k:root[k] for k in expected}==expected
    assert root['finalEstimate']==root['totalCost']!=root['p80']
    running=[data['pipelineStatus']['currentAgent'] for path,data in history
             if path=='estimates/e' and 'pipelineStatus' in data and data['pipelineStatus'].get('currentAgent')]
    assert list(dict.fromkeys(running))==AGENT_SEQUENCE


def test_stage_map_is_identity():
    assert FirestoreService.AGENT_TO_STAGE_MAP==dict(zip(AGENT_SEQUENCE,AGENT_SEQUENCE))

def test_local_a2a_default(monkeypatch):
    from config.settings import _get_default_a2a_url
    monkeypatch.setenv('USE_FIREBASE_EMULATORS','true')
    assert _get_default_a2a_url()=='http://127.0.0.1:5003/collabcanvas-dev/us-central1'

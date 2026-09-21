"""OFFLINE TEST FIXTURE — NOT GEMINI OUTPUT. No agents other than Final run."""
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from agents.primary.final_agent import FinalAgent
from config.errors import TrueCostError
from tests.fixtures import mock_risk_timeline_data as risk_data
from tests.fixtures.mock_cost_estimate_data import get_mock_location_output, get_mock_scope_output, get_valid_cost_output
from models.timeline import PhaseType

pytestmark = pytest.mark.usefixtures('block_network')

@pytest.fixture
def inputs(monkeypatch):
    class FixedDate(datetime):
        @classmethod
        def now(cls): return cls(2025, 1, 18)
    monkeypatch.setattr(risk_data, 'datetime', FixedDate)
    timeline = risk_data.get_valid_timeline_output()
    names = {t['id']: t['name'] for t in timeline['tasks']}
    specs = [dict(name=t['name'], phase=t['phase'], duration_days=t['duration'],
                  primary_trade=t['trade'], depends_on=[names[d] for d in t['dependencies']])
             for t in timeline['tasks']]
    assert len({s['name'] for s in specs}) == len(specs)
    for spec in specs:
        assert spec['name'].strip() and spec['primary_trade'].strip()
        assert type(spec['duration_days']) is int and spec['duration_days'] >= 1
        assert spec['phase'] in {p.value for p in PhaseType}
        assert all(d != spec['name'] and d in names.values() for d in spec['depends_on'])
    return dict(clarification_output=risk_data.get_mock_clarification_output(),
                location_output=get_mock_location_output(),scope_output=get_mock_scope_output(),
                cost_output=get_valid_cost_output(),risk_output=risk_data.get_valid_risk_output(),
                timeline_output=timeline,
                code_compliance_output={'codeSystem':'ICC','jurisdiction':{},'warnings':[],
                    'disclaimer':'OFFLINE TEST FIXTURE — NOT GEMINI OUTPUT'})

@pytest.fixture
def agent():
    storage=AsyncMock()
    storage.list_cost_items.return_value=[]
    llm=SimpleNamespace(generate_json=AsyncMock(return_value={'content':{
        'recommendations':[{'category':'schedule','title':'Review schedule','description':'Offline fixture review','priority':'medium'}],
        'key_assumptions':['OFFLINE TEST FIXTURE — NOT GEMINI OUTPUT'],
        'exclusions':[]},'tokens_used':0}))
    return FinalAgent(firestore_service=storage,llm_service=llm)

@pytest.mark.asyncio
async def test_complete(agent,inputs):
    out=await agent.run('offline-final',inputs)
    assert out['estimateComplete'] is True and out['estimateId']=='offline-final'
    c=out['costBreakdown']; e=out['executiveSummary']
    assert c['materials']==13158 and c['labor']==7500 and c['equipment']==450
    assert c['contingency']==3514.03
    assert c['totalWithContingency']==31456.74
    assert e['totalCost']==c['totalWithContingency']
    r=e['confidenceRange']; mc=inputs['risk_output']['monteCarlo']
    assert [r[k] for k in ('p50','p80','p90')]==[mc[k] for k in ('p50','p80','p90')]
    assert r['p50']<=e['totalCost']<=r['p90'] and r['p50']<=r['p80']<=r['p90']
    assert out['timeline']['totalDays']==inputs['timeline_output']['totalDuration']
    assert out['codeCompliance']['codeSystem']=='ICC'
    assert out['riskSummary']['topRisks']
    json.dumps(out,allow_nan=False)
    payload=agent.firestore.update_estimate.await_args.args[1]
    json.dumps(payload,allow_nan=False)
    assert payload['squareFootage']==196 and payload['address']=='Denver, CO, 80202'
    agent.firestore.save_agent_output.assert_awaited_once()
    agent.llm.generate_json.assert_awaited_once()

@pytest.mark.asyncio
async def test_schedule_handoff(agent,inputs):
    await agent.run('offline-final',inputs)
    schedule=agent.firestore.update_estimate.await_args.args[1]['schedule']
    assert schedule['tasks'][0]['start']==inputs['timeline_output']['tasks'][0]['start']
    assert schedule['tasks'][0]['end']==inputs['timeline_output']['tasks'][0]['end']
    assert schedule['tasks'][0]['is_milestone']==inputs['timeline_output']['tasks'][0]['isMilestone']

@pytest.mark.asyncio
async def test_cost_handoff(agent,inputs):
    await agent.run('offline-final',inputs)
    payload=agent.firestore.update_estimate.await_args.args[1]
    assert payload['costDrivers'][0]['name']=='Wood, Plastics, Composites'
    assert payload['costDrivers'][0]['cost']==4014
    division=payload['cost_breakdown']['divisions'][0]
    assert division['code']=='06' and division['total']==4014
    assert payload['cost_breakdown']['permits']==1125

@pytest.mark.asyncio
@pytest.mark.parametrize('missing',['clarification_output','location_output','scope_output','cost_output','risk_output','timeline_output'])
async def test_missing_section_existing_defaults(agent,inputs,missing):
    inputs.pop(missing)
    out=await agent.run('offline-final',inputs)
    assert out['estimateComplete']  # Document existing optional/default behavior.

@pytest.mark.asyncio
@pytest.mark.parametrize('bad',['timeline_type','empty_tasks','cost_type','percentiles','infinity','negative','duplicate_id','missing_id','estimate_id'])
async def test_reject_invalid(agent,inputs,bad):
    estimate='offline-final'
    if bad=='timeline_type': inputs['timeline_output']='invalid'
    if bad=='empty_tasks': inputs['timeline_output']['tasks']=[]
    if bad=='cost_type': inputs['cost_output']['total']='invalid'
    if bad=='percentiles': inputs['risk_output']['monteCarlo']['p80']=999999
    if bad=='infinity': inputs['cost_output']['subtotals']['materials']['low']=float('inf')
    if bad=='negative': inputs['cost_output']['subtotals']['materials']['low']=-1
    if bad=='duplicate_id': inputs['timeline_output']['tasks'][1]['id']=inputs['timeline_output']['tasks'][0]['id']
    if bad=='missing_id': inputs['timeline_output']['tasks'][0].pop('id')
    if bad=='estimate_id': estimate=''
    with pytest.raises((TrueCostError, ValueError, TypeError, AttributeError)):
        await agent.run(estimate,inputs)
    agent.firestore.save_agent_output.assert_not_awaited()

@pytest.mark.asyncio
async def test_risk_percentiles_preserved_in_root(agent,inputs):
    await agent.run('offline-final',inputs)
    payload=agent.firestore.update_estimate.await_args.args[1]
    assert [payload[k] for k in ('p50','p80','p90')]==[inputs['risk_output']['monteCarlo'][k] for k in ('p50','p80','p90')]

@pytest.mark.asyncio
async def test_firestore_encoding(agent,inputs):
    from google.cloud.firestore_v1._helpers import encode_dict
    out=await agent.run('offline-final',inputs)
    assert encode_dict(out)
    assert encode_dict(agent.firestore.update_estimate.await_args.args[1])

@pytest.mark.asyncio
async def test_scorer_critic_offline(agent,inputs):
    from agents.scorers.final_scorer import FinalScorer
    from agents.critics.final_critic import FinalCritic
    out=await agent.run('offline-final',inputs)
    scorer=FinalScorer(firestore_service=agent.firestore,llm_service=agent.llm)
    before=agent.llm.generate_json.await_count
    result=await scorer.score('offline-final',out,inputs)
    assert 0<=result['score']<=100
    assert agent.llm.generate_json.await_count==before
    critic=FinalCritic(firestore_service=agent.firestore,llm_service=agent.llm)
    agent.llm.generate_json.return_value={'content':{'issues':[],'why_wrong':'Offline review','how_to_fix':[]},'tokens_used':0}
    assert 'issues' in await critic.critique('offline-final',out,inputs,50,'offline')

@pytest.mark.asyncio
async def test_authoritative_cross_layer_contract(agent,inputs):
    from pathlib import Path
    from services.pdf_generator import _render_html
    expected=json.loads((Path(__file__).parents[1]/'fixtures/authoritative_cost_contract.json').read_text())
    out=await agent.run('offline-final',inputs)
    root=agent.firestore.update_estimate.await_args.args[1]
    assert {k:root[k] for k in expected}==expected
    assert root['baseEstimate'] == round(inputs['cost_output']['total']['low']-inputs['cost_output']['adjustments']['contingency']['low'],2)
    assert root['finalEstimate'] == round(root['baseEstimate']+root['contingency'],2)
    assert out['totalCost']==root['totalCost']==out['executiveSummary']['totalCost']
    for client_ready in (True,False):
        html=_render_html(root,{},['executive_summary'],client_ready,'offline-final')
        assert '$31,456.74' in html
        if not client_ready:
            assert '$29,283.60' in html and '$32,797.63' in html and '$35,067.77' in html
    json.dumps(root,allow_nan=False)

def test_pdf_does_not_invent_missing_percentiles():
    from services.pdf_generator import _render_html
    html=_render_html({'totalCost':31456.74},{},['executive_summary','risk_analysis'],False,'offline-final')
    assert 'Risk percentiles unavailable' in html
    assert '$34,602' not in html  # No synthesized 1.1x "P80".

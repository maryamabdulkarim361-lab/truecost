"""Deterministic user-endpoint security tests: no token verification network."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock
import pytest
from werkzeug.test import EnvironBuilder
from flask import Request
from services import request_auth as security

pytestmark=pytest.mark.usefixtures('block_network')

@pytest.fixture
def docs(monkeypatch):
    data={'projects/p':{'ownerId':'owner','collaborators':[{'userId':'editor','role':'editor'},{'userId':'viewer','role':'viewer'}]},
          'estimates/e':{'userId':'owner'}}
    def read(collection,identity):
        if not isinstance(identity,str) or not identity or '/' in identity:raise security.AccessError(400,'Invalid resource identifier')
        return data.get(f'{collection}/{identity}')
    monkeypatch.setattr(security,'document',read)
    monkeypatch.setattr(security.auth,'verify_id_token',lambda token:{'uid':token})
    return data

def request(body,header='Bearer owner'):
    return Request(EnvironBuilder(method='POST',json=body,headers={'Authorization':header}).get_environ())

def invoke(operation,body,header='Bearer owner'):
    @security.user_endpoint(operation)
    def handler(req):return SimpleNamespace(status_code=200,uid=security.current_uid())
    return handler(request(body,header))

@pytest.mark.parametrize('header',['','Basic fake','Bearer','Bearer fake extra'])
def test_missing_malformed(docs,header):assert invoke('read',{'estimateId':'e'},header).status_code==401

def test_invalid_token(docs,monkeypatch):
    def reject(_):raise ValueError('fake-sensitive-token-must-not-leak')
    monkeypatch.setattr(security.auth,'verify_id_token',reject)
    response=invoke('read',{'estimateId':'e'})
    assert response.status_code==401 and 'fake-sensitive' not in response.get_data(as_text=True)

@pytest.mark.parametrize('operation,body',[('start',{'projectId':'p','clarificationOutput':{}}),('read',{'estimateId':'e'}),('delete',{'estimateId':'e'}),('pdf',{'estimate_id':'e'})])
def test_owner_and_cross_user(docs,operation,body):
    assert invoke(operation,body).status_code==200
    assert invoke(operation,body,'Bearer attacker').status_code==403
    assert invoke(operation,{**body,'userId':'attacker'}).status_code==403

@pytest.mark.parametrize('user,status',[('editor',200),('viewer',403),('attacker',403)])
def test_project_roles(docs,user,status):
    assert invoke('start',{'projectId':'p','clarificationOutput':{}},f'Bearer {user}').status_code==status

def test_existing_id_cannot_be_reassigned(docs):
    body={'clarificationOutput':{'estimateId':'e'}}
    assert invoke('start',body,'Bearer attacker').status_code==403
    assert invoke('start',body).status_code==409

def test_estimate_owner_not_project_membership(docs):
    assert invoke('pdf',{'estimate_id':'e'},'Bearer editor').status_code==403

def test_context_does_not_leak(docs):
    assert invoke('read',{'estimateId':'e'}).uid=='owner'
    with pytest.raises(security.AccessError):security.current_uid()

def test_no_emulator_bypass(docs,monkeypatch):
    monkeypatch.setenv('USE_FIREBASE_EMULATORS','true')
    assert invoke('read',{'estimateId':'e'},'').status_code==401

@pytest.mark.parametrize('body',[{'estimateId':'missing'},{'estimateId':'../other'}])
def test_unavailable_resource(docs,body):assert invoke('read',body).status_code in (400,404)

def test_all_user_handlers_have_security_decorator():
    tree=ast.parse((Path(__file__).parents[2]/'main.py').read_text())
    expected={'start_deep_pipeline':'start','get_pipeline_status':'read','delete_estimate':'delete','generate_pdf':'pdf'}
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in expected:
            assert any(isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='user_endpoint' and d.args[0].value==expected[node.name] for d in node.decorator_list)
            assert not any(isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='user_id' for t in x.targets) and isinstance(x.value,ast.Call) and isinstance(x.value.func,ast.Attribute) and x.value.func.attr=='get' for x in ast.walk(node))

@pytest.mark.asyncio
async def test_atomic_create_only():
    from services.firestore_service import FirestoreService
    db=MagicMock(); ref=db.collection.return_value.document.return_value
    ref.create=AsyncMock()
    await FirestoreService(db).create_estimate('new','owner',{},create_only=True)
    ref.create.assert_awaited_once();ref.set.assert_not_called()

@pytest.mark.parametrize('name,operation,body',[('start_deep_pipeline','start',{'projectId':'p','clarificationOutput':{'estimateId':'new','projectBrief':{}}}),('get_pipeline_status','read',{'estimateId':'e'}),('delete_estimate','delete',{'estimateId':'e'}),('generate_pdf','pdf',{'estimate_id':'e'})])
def test_actual_endpoint_auth_boundary(docs,name,operation,body):
    """Execute actual decorated handler source with only its I/O coroutine mocked."""
    import asyncio
    from config.errors import TrueCostError,ErrorCode,ValidationError
    tree=ast.parse((Path(__file__).parents[2]/'main.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[d for d in node.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='user_endpoint']
    work=AsyncMock(return_value=(SimpleNamespace(pdf_url='offline',storage_path='offline',page_count=1,file_size_bytes=1,generated_at='offline') if operation=='pdf' else {'success':True,'estimateId':'new'}))
    namespace={'https_fn':SimpleNamespace(Request=object,Response=object), 'user_endpoint':security.user_endpoint,
        'get_user_id':lambda req:security.current_uid(), 'get_request_json':lambda req:req.get_json(),
        'asyncio':asyncio, 'logger':MagicMock(), 'TrueCostError':TrueCostError,'ErrorCode':ErrorCode,'ValidationError':ValidationError,
        '_json_response':lambda data,status=200:SimpleNamespace(status_code=status),
        'success_response':lambda data:data,'error_response':lambda *args:{},
        'validate_clarification_output':lambda data:SimpleNamespace(is_valid=True,raw_data=data),
        '_start_pipeline_async':work,'_get_status_async':work,'_delete_estimate_async':work,'_generate_pdf_async':work}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'actual_handler','exec'),namespace)
    handler=namespace[name]
    assert handler(request(body,'Bearer attacker')).status_code==403
    assert handler(request(body,'')).status_code==401
    work.assert_not_awaited()
    assert handler(request(body)).status_code==200
    work.assert_awaited_once()
    if operation=='start':assert work.await_args.kwargs['user_id']=='owner'

def test_a2a_deployment_is_private():
    tree=ast.parse((Path(__file__).parents[2]/'main.py').read_text())
    config=next(n.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='AGENT_ENDPOINT_CONFIG' for t in n.targets))
    assert any(isinstance(k,ast.Constant) and k.value=='invoker' and isinstance(v,ast.Constant) and v.value=='private' for k,v in zip(config.keys,config.values))

def test_browser_a2a_rejected_before_agent_construction():
    tree=ast.parse((Path(__file__).parents[2]/'main.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_handle_a2a_request')
    namespace={'https_fn':SimpleNamespace(Request=object,Response=object), '_json_response':lambda data,status=200:status}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'a2a','exec'),namespace)
    agent=MagicMock()
    assert namespace['_handle_a2a_request'](SimpleNamespace(method='POST',headers={'Origin':'http://untrusted.test'}),agent,'timeline')==403
    agent.assert_not_called()

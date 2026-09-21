"""No provider traffic: exercise the physical request boundary with fake HTTP."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from config.errors import StructuredError
from services import local_provider_budget as budget

pytestmark = pytest.mark.usefixtures('block_network')

@pytest.fixture
def counter(monkeypatch, tmp_path):
    for name, value in {
        'TRUECOST_LOCAL_PROVIDER_BUDGET':'test-008',
        'USE_FIREBASE_EMULATORS':'true',
        'FIRESTORE_EMULATOR_HOST':'127.0.0.1:8081',
        'PIPELINE_EXECUTION_MODE':'synchronous', 'LLM_PROVIDER':'gemini',
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('K_SERVICE', raising=False)
    monkeypatch.delenv('APP_ENV', raising=False)
    path = tmp_path / 'counter'
    path.write_bytes(b'0')
    path.chmod(0o600)
    monkeypatch.setattr(budget, 'COUNTER', path)
    return path


def test_default_unchanged(monkeypatch):
    monkeypatch.delenv('TRUECOST_LOCAL_PROVIDER_BUDGET', raising=False)
    budget.consume()

@pytest.mark.parametrize('name,value', [
    ('APP_ENV','production'), ('K_SERVICE','worker'),
    ('USE_FIREBASE_EMULATORS','false'), ('PIPELINE_EXECUTION_MODE','durable'),
    ('LLM_PROVIDER','openai'), ('FIRESTORE_EMULATOR_HOST','other:8081'),
    ('TRUECOST_LOCAL_PROVIDER_BUDGET','typo'),
])
def test_reject_unsafe_configuration(counter, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(StructuredError): budget.consume()
    assert counter.read_bytes() == b'0'

@pytest.mark.parametrize('value', [b'', b'20', b'-1', b'1\n', b'00', b'x'*100])
def test_corrupt_or_exhausted_closed(counter, value):
    counter.write_bytes(value)
    with pytest.raises(StructuredError) as caught: budget.consume()
    assert caught.value.details['retryable'] is False


def test_missing_closed(counter):
    counter.unlink()
    with pytest.raises(StructuredError): budget.consume()
    assert not counter.exists()


def test_concurrent_attempts_share_twenty(counter):
    def attempt(_):
        try: budget.consume(); return 1
        except StructuredError: return 0
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(attempt, range(70))) == 20
    assert counter.read_bytes() == b'20'


def test_new_loops_no_reset_and_no_twenty_first_http(counter):
    sent = []
    async def request():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: sent.append(True) or httpx.Response(200)),
            event_hooks={'request':[budget.before_request]}, trust_env=False,
        ) as client:
            await client.get('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    for _ in range(20): asyncio.run(request())
    with pytest.raises(StructuredError): asyncio.run(request())
    assert len(sent) == 20


def test_counter_permissions_and_symlink(counter):
    counter.chmod(0o644)
    with pytest.raises(StructuredError): budget.consume()
    target = counter.with_name('target')
    counter.rename(target)
    counter.symlink_to(target)
    with pytest.raises(StructuredError): budget.consume()


def test_service_installs_hook_when_enabled(counter, monkeypatch):
    from services import llm_service
    service = llm_service.LLMService(api_key='offline-fake')
    async def lifetime():
        async with service:
            client = service._get_http_async_client()
            assert client.event_hooks['request'] == [budget.before_request]
    asyncio.run(lifetime())
    assert counter.read_bytes() == b'0'


def test_control_state_does_not_consume(counter):
    assert budget.control_state() == {'enabled':True,'maximum_requests':20}
    assert counter.read_bytes() == b'0'


def test_http_failure_slot_is_not_refunded(counter):
    def fail(request): raise httpx.ConnectError('synthetic')
    async def attempt():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail),
            event_hooks=budget.transport_options()['event_hooks']) as client:
            await client.get('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions')
    with pytest.raises(httpx.ConnectError): asyncio.run(attempt())
    assert counter.read_bytes() == b'1'


def test_new_process_cannot_reset_budget(counter):
    import os, subprocess, sys
    env = {name:os.environ[name] for name in (
        'TRUECOST_LOCAL_PROVIDER_BUDGET','USE_FIREBASE_EMULATORS',
        'FIRESTORE_EMULATOR_HOST','PIPELINE_EXECUTION_MODE','LLM_PROVIDER')}
    from pathlib import Path
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2])
    script = ('from pathlib import Path; from services import local_provider_budget as b; '
              'import sys; b.COUNTER=Path(sys.argv[1]); b.consume()')
    counter.write_bytes(b'19')
    result=subprocess.run([sys.executable,'-B','-c',script,str(counter)],env=env,
                          capture_output=True,timeout=15)
    assert result.returncode == 0
    assert counter.read_bytes() == b'20'
    with pytest.raises(StructuredError): budget.consume()


def test_guard_rejects_openai_endpoint_before_send(counter):
    sent=[]
    async def request():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req:sent.append(True) or httpx.Response(200)),
            event_hooks=budget.transport_options()['event_hooks']) as client:
            await client.get('https://api.openai.com/v1/chat/completions')
    with pytest.raises(StructuredError): asyncio.run(request())
    assert sent == [] and counter.read_bytes() == b'0'


def test_local_health_attests_control_without_consumption(counter):
    import ast
    from pathlib import Path
    from flask import Flask, jsonify
    tree=ast.parse((Path(__file__).resolve().parents[2]/'serve_local.py').read_text())
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='health')
    function.decorator_list=[]
    namespace={'jsonify':jsonify}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'health','exec'),namespace)
    with Flask('offline').app_context():
        result=namespace['health']().get_json()
    assert result['local_provider_budget'] == {'enabled':True,'maximum_requests':20}
    assert counter.read_bytes() == b'0'


def test_sdk_wrapped_budget_error_stays_terminal(counter):
    import openai
    from services.llm_errors import classify_llm_error
    counter.write_bytes(b'20')
    try: budget.consume()
    except StructuredError as blocked:
        wrapped=openai.APIConnectionError(request=httpx.Request('POST','https://offline.invalid'))
        wrapped.__cause__=blocked
        error=classify_llm_error(wrapped,provider='gemini',model='offline')
    assert error.details['reason']=='validation_budget_blocked'
    assert error.details['retryable'] is False
    assert 'offline.invalid' not in str(error.to_dict())


def test_installed_sdk_preserves_budget_denial_and_closes(counter, monkeypatch):
    from services import llm_service
    from types import SimpleNamespace
    monkeypatch.setattr(llm_service,'settings',SimpleNamespace(
        llm_model='offline',llm_provider='gemini',llm_temperature=0.1,
        llm_client_options={'base_url':'https://generativelanguage.googleapis.com/v1beta/openai/',
                            'use_responses_api':False}))
    sent=[]
    monkeypatch.setattr(llm_service,'DefaultAsyncHttpxClient',lambda **options:
        httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req:sent.append(True) or httpx.Response(500)),trust_env=False,**options))
    counter.write_bytes(b'20')
    service=llm_service.LLMService(api_key='offline-fake')
    async def request():
        async with service: await service.generate_with_system_prompt('offline','offline')
    with pytest.raises(StructuredError) as caught: asyncio.run(request())
    assert caught.value.details['reason']=='validation_budget_blocked'
    assert caught.value.details['retryable'] is False
    assert sent == [] and service._http_async_client is None

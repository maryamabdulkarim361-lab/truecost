"""Offline regression tests for request-owned LLM transports."""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.usefixtures("block_network")


@pytest.fixture(params=['openai', 'gemini'])
def fake_clients(monkeypatch, request):
    from services import llm_service
    from config.settings import Settings
    configuration = Settings(llm_provider=request.param)
    monkeypatch.setattr(llm_service.settings, 'llm_client_options', configuration.llm_client_options)
    monkeypatch.setattr(llm_service.settings, 'llm_model',
                        'gemini-3.6-flash' if request.param == 'gemini' else 'gpt-4o')

    transports = []
    models = []

    class Transport:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.closed = False
            self.close_count = 0
            self.close_error = None
            transports.append(self)

        async def aclose(self):
            assert asyncio.get_running_loop() is self.loop
            assert not self.loop.is_closed()
            self.closed = True
            self.close_count += 1
            if self.close_error:
                raise self.close_error

    class Model:
        def __init__(self, **kwargs):
            self.options = kwargs
            assert kwargs["max_retries"] == 0
            self.transport = kwargs.get('http_async_client')
            self.async_client = kwargs.get('async_client') or SimpleNamespace(create=self.ainvoke)
            self.root_async_client = kwargs.get('root_async_client') or SimpleNamespace(
                chat=SimpleNamespace(completions=self.async_client)
            )
            self.error = None
            models.append(self)

        async def ainvoke(self, messages, **kwargs):
            if self.transport is None:
                return await self.async_client.create(messages, **kwargs)
            assert asyncio.get_running_loop() is self.transport.loop
            assert not self.transport.closed
            if self.error:
                raise self.error
            return SimpleNamespace(
                content='{"ok": true}',
                response_metadata={'token_usage': {'total_tokens': 7}},
            )

    def no_network(*args, **kwargs):
        pytest.fail('A lifecycle unit test attempted network access')

    monkeypatch.setattr('socket.socket.connect', no_network)
    monkeypatch.setattr('socket.socket.connect_ex', no_network)
    monkeypatch.setattr(llm_service, 'DefaultAsyncHttpxClient', Transport)
    monkeypatch.setattr(llm_service, 'ChatOpenAI', Model)
    return llm_service, transports, models


@pytest.mark.parametrize('failure', [False, True])
def test_request_closes_before_loop_exit(fake_clients, failure):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')

    async def request():
        async with service:
            model = service.client
            if failure:
                model.error = RuntimeError('simulated generation failure')
            result = await service.generate_json('system', 'user')
            assert result == {'content': {'ok': True}, 'tokens_used': 7}

    if failure:
        from config.errors import TrueCostError
        with pytest.raises(TrueCostError, match='LLM provider request failed'):
            asyncio.run(request())
    else:
        asyncio.run(request())
    assert models[0].transport is transports[0]
    assert transports[0].closed and transports[0].close_count == 1
    assert service._client is None


def test_sync_construction_defers_transport_until_async_use(fake_clients):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')
    default = service.client
    alternate = service.create_chat_model(model='alternate', temperature=0.5)
    assert default is service.client
    assert transports == []

    async def request():
        async with service:
            await default.ainvoke([])
            await alternate.ainvoke([])
            assert len(transports) == 1
            assert transports[0].loop is asyncio.get_running_loop()
            assert not transports[0].closed

    asyncio.run(request())
    assert transports[0].closed
    with pytest.raises(RuntimeError, match='closed service lifetime'):
        asyncio.run(default.ainvoke([]))


def test_real_langchain_sync_and_deferred_async_with_fake_sdk(fake_clients, monkeypatch):
    """Exercise installed LangChain construction/invocation, never its network SDK."""
    from langchain_openai import ChatOpenAI
    from langchain_openai.chat_models import base

    module, transports, _ = fake_clients
    response = MagicMock()
    response.parse.return_value = {
        'choices': [{'message': {'role': 'assistant', 'content': 'offline'},
                     'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
    }
    sync_sdk = MagicMock()
    sync_sdk.chat.completions.with_raw_response.create.return_value = response
    async_sdk = MagicMock()
    async_sdk.chat.completions.with_raw_response.create = AsyncMock(return_value=response)
    async_factory = MagicMock(return_value=async_sdk)
    monkeypatch.setattr(module, 'ChatOpenAI', ChatOpenAI)
    monkeypatch.setattr(base.openai, 'OpenAI', MagicMock(return_value=sync_sdk))
    monkeypatch.setattr(base.openai, 'AsyncOpenAI', async_factory)

    def forbid_default(*args, **kwargs):
        pytest.fail('LangChain default async transport cache was used')

    monkeypatch.setattr(base, '_get_default_async_httpx_client', forbid_default)
    # Sync HTTP is also fake, so construction never needs a real HTTP client.
    monkeypatch.setattr(base, '_get_default_httpx_client', MagicMock())
    service = module.LLMService(api_key='offline-test-placeholder')
    model = service.client
    alternate = service.create_chat_model()
    if service._provider_options:
        assert model.openai_api_base == service._provider_options['base_url']
        assert model.use_responses_api is False
        assert model.model_name == 'gemini-3.6-flash'
    assert model.invoke('offline').content == 'offline'
    assert alternate.invoke('offline').content == 'offline'
    assert not transports
    async_factory.assert_not_called()

    async def request():
        async with service:
            assert (await model.ainvoke('offline')).content == 'offline'
            assert (await alternate.ainvoke('offline')).content == 'offline'
            assert len(transports) == 1

    asyncio.run(request())
    assert transports[0].closed
    assert all(call.kwargs['http_client'] is transports[0]
               for call in async_factory.call_args_list)

    # A retained real model must also reject a different loop, even without
    # going back through the service.client property.
    owner = asyncio.new_event_loop()
    other = asyncio.new_event_loop()

    async def create():
        return service.create_chat_model()

    try:
        retained = owner.run_until_complete(create())
        with pytest.raises(RuntimeError, match='before changing event loops'):
            other.run_until_complete(retained.ainvoke('offline'))
        owner.run_until_complete(service.aclose())
        with pytest.raises(RuntimeError, match='closed service lifetime'):
            other.run_until_complete(retained.ainvoke('offline'))
    finally:
        owner.close()
        other.close()


def test_cached_client_rejects_unclosed_cross_loop_use(fake_clients):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')
    owner = asyncio.new_event_loop()
    other = asyncio.new_event_loop()

    async def access():
        return service.client

    try:
        model = owner.run_until_complete(access())
        assert owner.run_until_complete(access()) is model
        with pytest.raises(RuntimeError, match='before changing event loops'):
            other.run_until_complete(access())
        assert len(transports) == 1
        owner.run_until_complete(service.aclose())
        assert other.run_until_complete(access()) is not model
        other.run_until_complete(service.aclose())
    finally:
        owner.close()
        other.close()


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_close_idempotency_and_reference_reset(fake_clients, cleanup_fails):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')

    async def request():
        service.client
        if cleanup_fails:
            transports[0].close_error = RuntimeError('cleanup failed')
            with pytest.raises(RuntimeError, match='cleanup failed'):
                await service.aclose()
        else:
            await service.aclose()
        assert service._client is None
        assert service._http_async_client is None
        assert service._owner_loop is None
        await service.aclose()
        assert transports[0].close_count == 1
        async with service:
            await service.generate([])
        assert len(transports) == 2

    asyncio.run(request())


def test_cleanup_failure_preserves_generation_exception(fake_clients):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')

    async def request():
        async with service:
            service.client.error = RuntimeError('generation failed')
            transports[0].close_error = RuntimeError('cleanup failed')
            await service.generate([])

    from config.errors import TrueCostError
    with pytest.raises(TrueCostError, match='LLM provider request failed'):
        asyncio.run(request())
    assert service._http_async_client is None
    assert service._client is None


@pytest.mark.parametrize('failure', [False, True])
def test_direct_integration_critic_cleanup(fake_clients, failure):
    """Execute the integration runner's actual critic ownership block offline."""
    module, transports, models = fake_clients
    path = Path(__file__).resolve().parents[2] / 'run_integration_test.py'
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == 'run_agent_with_validation')
    block = next(n for n in ast.walk(function) if isinstance(n, ast.AsyncWith)
                 and ast.unparse(n.items[0].context_expr) == 'critic.llm')
    wrapper = ast.parse('async def exercise():\n    pass').body[0]
    wrapper.body = [block]

    class Critic:
        def __init__(self):
            self.llm = module.LLMService(api_key='offline-test-placeholder')

        async def critique(self, **kwargs):
            if failure:
                self.llm.client.error = RuntimeError('critic failed')
            return await self.llm.generate([])

    namespace = dict(critic=Critic(), estimate_id='offline', output={},
                     accumulated_context={}, score=0, feedback={})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
                 str(path), 'exec'), namespace)
    if failure:
        from config.errors import TrueCostError
        with pytest.raises(TrueCostError, match='LLM provider request failed'):
            asyncio.run(namespace['exercise']())
    else:
        asyncio.run(namespace['exercise']())
    assert transports[0].closed and transports[0].close_count == 1
    assert namespace['critic'].llm._client is None


def test_repeated_loops_and_factory_ownership(fake_clients):
    module, transports, models = fake_clients
    service = module.LLMService(api_key='offline-test-placeholder')

    async def request():
        async with service:
            await service.generate([])
            alternate = service.create_chat_model(model='alternate', temperature=0.5)
            assert alternate.transport is service.client.transport
            assert alternate.options['model'] == 'alternate'
            assert alternate.options['temperature'] == 0.5
            await alternate.ainvoke([])

    asyncio.run(request())
    asyncio.run(request())
    assert len(transports) == 2
    assert transports[0] is not transports[1]
    assert transports[0].loop is not transports[1].loop
    assert all(t.closed and t.close_count == 1 for t in transports)
    assert service.total_tokens_used == 14


@pytest.mark.parametrize('handler_name', ['_handle_a2a_request', '_create_a2a_handler'])
@pytest.mark.parametrize('failure', [False, True])
def test_actual_a2a_handler_owns_service(fake_clients, handler_name, failure):
    """Run actual handler source without importing Firebase or starting servers."""
    module, transports, models = fake_clients
    path = Path(__file__).resolve().parents[2] / 'main.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == handler_name)
    namespace = {
        'asyncio': asyncio, 'Dict': Dict, 'Any': Any,
        'https_fn': SimpleNamespace(Request=object, Response=object),
        'get_request_json': lambda req: {'id': 'offline', 'jsonrpc': '2.0', 'method': 'message/send', 'params': {}},
        '_json_response': lambda data, status=200: (status, data),
        'logger': MagicMock(),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)

    class Agent:
        def __init__(self):
            self.llm = module.LLMService(api_key='offline-test-placeholder')

        async def handle_a2a_request(self, data):
            if failure:
                self.llm.client.error = RuntimeError('simulated generation failure')
            return await self.llm.generate([])

    request = SimpleNamespace(method='POST')
    handler = namespace[handler_name]
    if handler_name == '_create_a2a_handler':
        result = handler(Agent, 'offline')(request)
    else:
        result = handler(request, Agent, 'offline')
    assert result[0] == (500 if failure else 200)
    assert transports[0].closed and transports[0].close_count == 1

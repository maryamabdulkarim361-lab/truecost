"""Provider configuration and response contracts, without external requests."""
import ast
import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.usefixtures('block_network')
ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/openai/'


@pytest.fixture
def configuration(monkeypatch):
    module = importlib.import_module('config.settings')
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-openai-placeholder')
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-gemini-placeholder')
    monkeypatch.setenv('LLM_MODEL', 'gemini-3.6-flash')
    monkeypatch.delenv('LLM_PROVIDER', raising=False)
    monkeypatch.setattr('config.secrets.get_openai_api_key',
                        lambda: os.getenv('OPENAI_API_KEY'))
    return module


def test_default_and_invalid_provider(configuration, monkeypatch):
    monkeypatch.delenv('LLM_MODEL', raising=False)
    settings = configuration.Settings()
    assert settings.llm_provider == 'openai'
    assert settings.llm_model == 'gpt-4o'
    assert settings.llm_client_options == {}
    monkeypatch.setenv('LLM_PROVIDER', 'unsupported')
    with pytest.raises(ValueError, match='LLM_PROVIDER'):
        configuration.Settings()


@pytest.mark.parametrize('provider,selected,other', [
    ('openai', 'OPENAI_API_KEY', 'GEMINI_API_KEY'),
    ('gemini', 'GEMINI_API_KEY', 'OPENAI_API_KEY'),
])
def test_credential_isolation(configuration, monkeypatch, provider, selected, other):
    from services import llm_service
    monkeypatch.setenv('LLM_PROVIDER', provider)
    monkeypatch.delenv(selected)
    settings = configuration.Settings(use_firebase_emulators=False)
    assert settings.llm_api_key is None
    with pytest.raises(ValueError, match=selected):
        settings.validate()
    monkeypatch.setattr(llm_service, 'settings', settings)
    with pytest.raises(ValueError, match=selected):
        llm_service.LLMService()
    monkeypatch.setenv(selected, 'offline-selected-placeholder')
    monkeypatch.delenv(other)
    assert settings.llm_api_key == 'offline-selected-placeholder'
    settings.validate()


def test_gemini_constructor_options(configuration, monkeypatch):
    from services import llm_service
    settings = configuration.Settings(llm_provider='gemini')
    monkeypatch.setattr(llm_service, 'settings', settings)
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://invalid.local')
    monkeypatch.setenv('OPENAI_API_BASE', 'http://invalid.local')
    constructor = MagicMock()
    monkeypatch.setattr(llm_service, 'ChatOpenAI', constructor)
    transport = MagicMock(aclose=AsyncMock())
    factory = MagicMock(return_value=transport)
    monkeypatch.setattr(llm_service, 'DefaultAsyncHttpxClient', factory)
    service = llm_service.LLMService()
    service.client
    factory.assert_not_called()
    assert constructor.call_args.kwargs['base_url'] == ENDPOINT
    assert constructor.call_args.kwargs['use_responses_api'] is False
    assert constructor.call_args.kwargs['model'] == 'gemini-3.6-flash'
    async def request():
        async with service:
            service.create_chat_model()
            options = constructor.call_args.kwargs
            assert options['http_async_client'] is transport
            assert options['base_url'] == ENDPOINT
            assert options['use_responses_api'] is False
    asyncio.run(request())
    transport.aclose.assert_awaited_once()


@pytest.mark.parametrize('provider', ['openai', 'gemini'])
@pytest.mark.parametrize('normalized', [True, False])
def test_response_contracts(configuration, monkeypatch, provider, normalized):
    from services import llm_service
    monkeypatch.setattr(llm_service, 'settings', configuration.Settings(llm_provider=provider))
    service = llm_service.LLMService()
    response = SimpleNamespace(content='```json\n{"ok": true}\n```',
        usage_metadata={'total_tokens': 12} if normalized else None,
        response_metadata={'token_usage': {'total_tokens': 12}})
    service._client = SimpleNamespace(ainvoke=AsyncMock(return_value=response))
    async def request():
        async with service:
            result = await service.generate_json('system', 'user', max_tokens=1400)
            assert result == {'content': {'ok': True}, 'tokens_used': 12}
            assert service.total_tokens_used == 12
            assert service._client.ainvoke.call_args.kwargs['max_tokens'] == 1400
    asyncio.run(request())


def test_neutral_errors_and_nontext_response(configuration, monkeypatch):
    from services import llm_service
    from config.errors import TrueCostError
    monkeypatch.setattr(llm_service, 'settings', configuration.Settings(llm_provider='gemini'))
    service = llm_service.LLMService()
    async def request():
        async with service:
            service._client = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError('rate_limit')))
            with pytest.raises(TrueCostError, match='LLM provider request failed'):
                await service.generate([])
            service._client.ainvoke = AsyncMock(return_value=SimpleNamespace(content=[]))
            with pytest.raises(TrueCostError, match='LLM response is invalid'):
                await service.generate([])
    asyncio.run(request())


@pytest.mark.parametrize('provider,key,other', [
    ('openai', 'OPENAI_API_KEY', 'GEMINI_API_KEY'),
    ('gemini', 'GEMINI_API_KEY', 'OPENAI_API_KEY'),
])
def test_local_startup_validation(configuration, monkeypatch, provider, key, other, capsys):
    path = Path(__file__).resolve().parents[2] / 'serve_local.py'
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == 'validate_local_llm_environment')
    namespace = {'os': os, 'settings': configuration.Settings(llm_provider=provider)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    validate = namespace['validate_local_llm_environment']
    monkeypatch.delenv(key)
    with pytest.raises(RuntimeError, match=key):
        validate()
    monkeypatch.setenv(key, 'offline-selected-placeholder')
    monkeypatch.delenv(other)
    validate()
    assert capsys.readouterr().out == ''

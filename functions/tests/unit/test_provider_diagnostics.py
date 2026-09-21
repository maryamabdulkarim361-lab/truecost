"""Offline bounded provider diagnostics; all payloads are synthetic."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import openai
import pytest
from config.errors import ErrorCode, StructuredError
from services.llm_errors import classify_llm_error, MAX_CHAIN_DEPTH
from services.llm_service import LLMService

pytestmark = pytest.mark.usefixtures('block_network')
SECRET = 'fake-sensitive-body-prompt-https://example.invalid?key=fake'


def http_error(status, body=None):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://offline.invalid'))
    return openai.APIStatusError(SECRET, response=response, body=body)


def classify(error, stage='provider_request'):
    return classify_llm_error(error, provider='gemini', model='gemini-3.6-flash', failure_stage=stage)


@pytest.mark.parametrize('wrapped', [False, True])
@pytest.mark.parametrize('kind,code,reason', [
    ('quota', 'LLM_QUOTA_EXCEEDED', None), ('rate', 'LLM_RATE_LIMITED', None),
    ('401', 'LLM_AUTH_ERROR', None), ('403', 'LLM_AUTH_ERROR', None),
    ('timeout', 'LLM_TIMEOUT', None), ('transport', 'LLM_PROVIDER_ERROR', 'transport_error'),
    ('500', 'LLM_PROVIDER_ERROR', 'provider_http_error'),
    ('503', 'LLM_PROVIDER_ERROR', 'provider_http_error'),
    ('validation', 'LLM_INVALID_RESPONSE', None),
])
def test_classification(kind, code, reason, wrapped):
    if kind == 'quota': error = http_error(429, {'type': 'insufficient_quota', 'message': SECRET})
    elif kind == 'rate': error = http_error(429, {})
    elif kind == 'timeout': error = httpx.ReadTimeout(SECRET)
    elif kind == 'transport': error = httpx.ConnectError(SECRET)
    elif kind == 'validation':
        error = openai.APIResponseValidationError(response=httpx.Response(200,
            request=httpx.Request('POST', 'https://offline.invalid')), body={'secret': SECRET})
    else: error = http_error(int(kind))
    if wrapped:
        outer = RuntimeError(SECRET)
        outer.__cause__ = error
        error = outer
    result = classify(error)
    assert result.code == code
    if reason: assert result.details['reason'] == reason
    if kind in ('500', '503'): assert result.details['http_status'] == int(kind)
    restored = StructuredError.from_dict(result.to_dict())
    assert restored.to_dict() == result.to_dict()
    assert SECRET not in json.dumps(result.to_dict())


def test_context_cycle():
    first, second = RuntimeError(SECRET), ValueError(SECRET)
    first.__cause__ = second
    second.__context__ = first
    second.__cause__ = http_error(401)
    assert classify(first).code == ErrorCode.LLM_AUTH_ERROR


def test_depth_limit():
    error = http_error(401)
    for _ in range(MAX_CHAIN_DEPTH + 1):
        outer = RuntimeError(SECRET)
        outer.__cause__ = error
        error = outer
    assert classify(error).code == ErrorCode.LLM_PROVIDER_ERROR
    assert classify(error.__cause__).code == ErrorCode.LLM_AUTH_ERROR


def test_unknown_and_lifecycle():
    assert classify(ValueError(SECRET)).details['reason'] == 'unclassified_error'
    error = classify(RuntimeError(SECRET), 'client_initialization')
    assert error.details['reason'] == 'client_lifecycle_error'


@pytest.mark.asyncio
async def test_local_usage_processing():
    service = LLMService(api_key='offline-fake')
    service._client = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content=SECRET, usage_metadata={'total_tokens': 'invalid'})))
    with pytest.raises(StructuredError) as caught:
        await service.generate([])
    assert caught.value.details['reason'] == 'response_processing_error'
    assert caught.value.details['failure_stage'] == 'response_processing'
    assert SECRET not in json.dumps(caught.value.to_dict())


def test_allowlist():
    error = StructuredError.from_dict({'code': 'LLM_PROVIDER_ERROR', 'message': SECRET,
        'details': {'reason': SECRET, 'failure_stage': SECRET, 'body': SECRET, 'url': SECRET}})
    assert SECRET not in json.dumps(error.to_dict())
    assert 'failure_stage' not in error.details


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['timeline_prompt_construction', 'timeline_processing'])
async def test_timeline_local_stage(stage):
    from agents.primary.timeline_agent import TimelineAgent
    llm = SimpleNamespace(generate_json=AsyncMock(return_value=None))
    agent = TimelineAgent(firestore_service=AsyncMock(), llm_service=llm)
    scope = [] if stage == 'timeline_prompt_construction' else {}
    with pytest.raises(StructuredError) as caught:
        await agent._generate_task_specs_with_llm('offline', 'kitchen_remodel', 196,
                                                scope, {}, {}, None)
    assert caught.value.code == ErrorCode.LLM_PROVIDER_ERROR
    assert caught.value.details['failure_stage'] == stage
    assert caught.value.details['reason'] == 'unclassified_error'

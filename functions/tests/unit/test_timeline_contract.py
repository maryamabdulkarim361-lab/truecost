"""Offline Timeline contract and sanitized diagnostics regression tests."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from config.errors import ErrorCode, StructuredError
from agents.primary.timeline_agent import TimelineAgent, TIMELINE_TASK_PLANNER_PROMPT
from services.llm_service import LLMService

pytestmark = pytest.mark.usefixtures('block_network')


def task():
    return dict(name='offline-sensitive-marker', phase='finish', duration_days=2,
                primary_trade='carpenter', depends_on=[])


async def plan(content):
    agent = TimelineAgent(firestore_service=AsyncMock(), llm_service=SimpleNamespace(
        generate_json=AsyncMock(return_value={'content': content, 'tokens_used': 0})))
    return await agent._generate_task_specs_with_llm('offline', 'kitchen_remodel', 196, {}, {}, {}, None)


@pytest.mark.asyncio
async def test_valid_unchanged():
    content = {'tasks': [task()]}
    original = copy.deepcopy(content)
    assert await plan(content) == original['tasks']
    assert content == original


@pytest.mark.asyncio
@pytest.mark.parametrize('content,reason,path', [
    ({}, 'tasks_missing', 'tasks'),
    ({'tasks': []}, 'tasks_empty', 'tasks'),
    ({'tasks': None}, 'invalid_type', 'tasks'),
    ({'tasks': [None]}, 'invalid_type', 'tasks[0]'),
    ({'tasks': [task(), task()]}, 'duplicate', 'tasks[1].name'),
    ({'status': 'insufficient_information', 'tasks': []}, 'conflicting_state', 'response'),
    ({'status': 'invented'}, 'invalid', 'status'),
])
async def test_shapes(content, reason, path):
    with pytest.raises(StructuredError) as caught:
        await plan(content)
    assert caught.value.code == ErrorCode.LLM_INVALID_RESPONSE
    assert caught.value.details['reason'] == reason
    assert caught.value.details['field_path'] == path


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value,reason', [
    ('duration_days', None, 'null'), ('duration_days', '2', 'invalid_type'),
    ('duration_days', True, 'invalid_type'), ('duration_days', 2.5, 'invalid_type'),
    ('duration_days', 0, 'out_of_range'), ('duration_days', -1, 'out_of_range'),
    ('phase', 'offline-sensitive-marker', 'invalid'), ('phase', [], 'invalid'),
    ('primary_trade', ' ', 'blank'), ('depends_on', 'bad', 'invalid_type'),
    ('depends_on', [None], 'invalid_type'), ('depends_on', ['unknown'], 'unknown_task'),
    ('depends_on', ['offline-sensitive-marker'], 'self_dependency'),
])
async def test_task_diagnostics(field, value, reason):
    item = task()
    item[field] = value
    before = copy.deepcopy(item)
    with pytest.raises(StructuredError) as caught:
        await plan({'tasks': [item]})
    assert item == before  # Never invent/coerce a duration or discard a bad task.
    error = StructuredError.from_dict(caught.value.to_dict())
    assert error.code == ErrorCode.LLM_INVALID_RESPONSE
    assert error.details['reason'] == reason
    assert error.details['field_path'] == f'tasks[0].{field}'
    assert 'offline-sensitive-marker' not in json.dumps(error.to_dict())


@pytest.mark.asyncio
async def test_explicit_insufficiency():
    with pytest.raises(StructuredError) as caught:
        await plan({'status': 'insufficient_information'})
    assert caught.value.code == ErrorCode.INSUFFICIENT_DATA
    assert caught.value.details['reason'] == 'insufficient_scheduling_information'


def test_prompt_contract():
    assert 'Instead set duration_days to null' not in TIMELINE_TASK_PLANNER_PROMPT
    assert 'integer working days (>= 1)' in TIMELINE_TASK_PLANNER_PROMPT
    assert '{"status": "insufficient_information"}' in TIMELINE_TASK_PLANNER_PROMPT


@pytest.mark.asyncio
async def test_invalid_json_diagnostic():
    service = LLMService(api_key='offline-fake-key')
    service.generate_with_system_prompt = AsyncMock(return_value={
        'content': 'offline-sensitive-marker: invalid JSON', 'tokens_used': 0})
    with pytest.raises(StructuredError) as caught:
        await service.generate_json('offline', 'offline')
    assert caught.value.details['reason'] == 'invalid_json'
    assert caught.value.details['field_path'] == 'response'
    assert 'offline-sensitive-marker' not in json.dumps(caught.value.to_dict())


def test_untrusted_diagnostics_dropped():
    error = StructuredError.from_dict({'code': 'LLM_INVALID_RESPONSE', 'details': {
        'reason': 'offline-sensitive-marker', 'field_path': 'https://example.invalid?key=fake'}})
    assert 'reason' not in error.details
    assert 'field_path' not in error.details


@pytest.mark.asyncio
async def test_timeline_completion_budget_one_generation():
    from agents.primary.timeline_agent import TIMELINE_TASK_PLAN_MAX_TOKENS
    llm = SimpleNamespace(generate_json=AsyncMock(return_value={
        'content': {'tasks': [task()]}, 'tokens_used': 0}))
    agent = TimelineAgent(firestore_service=AsyncMock(), llm_service=llm)
    result = await agent._generate_task_specs_with_llm(
        'offline', 'kitchen_remodel', 196, {}, {}, {}, None)
    assert result == [task()]
    assert TIMELINE_TASK_PLAN_MAX_TOKENS == 4096
    llm.generate_json.assert_awaited_once()
    assert llm.generate_json.call_args.kwargs == {'max_tokens': 4096}


@pytest.mark.asyncio
async def test_timeline_length_failure_one_generation_no_repair():
    service = LLMService(api_key='offline-fake')
    invoke = AsyncMock(return_value=SimpleNamespace(
        content='{"tasks":[', usage_metadata={'total_tokens': 4096},
        response_metadata={'finish_reason': 'length'}))
    service._client = SimpleNamespace(ainvoke=invoke)
    storage = AsyncMock()
    agent = TimelineAgent(firestore_service=storage, llm_service=service)
    with pytest.raises(StructuredError) as caught:
        await agent._generate_task_specs_with_llm(
            'offline', 'kitchen_remodel', 196, {}, {}, {}, None)
    assert caught.value.code == ErrorCode.LLM_INVALID_RESPONSE
    assert caught.value.details['reason'] == 'invalid_json'
    assert caught.value.details['completion_status'] == 'length'
    assert caught.value.details['failure_stage'] == 'response_processing'
    invoke.assert_awaited_once()
    assert invoke.call_args.kwargs == {'max_tokens': 4096}
    storage.save_agent_output.assert_not_awaited()
    await service.aclose()


@pytest.mark.asyncio
async def test_installed_gemini_wire_budget_without_http():
    import httpx
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage
    # Explicit fake key and owned transports; construct/serialize only, no invoke.
    with httpx.Client(trust_env=False) as sync:
        async with httpx.AsyncClient(trust_env=False) as asynchronous:
            model = ChatOpenAI(model='gemini-3.6-flash', api_key='offline-fake',
                base_url='https://generativelanguage.googleapis.com/v1beta/openai/',
                use_responses_api=False, max_retries=0,
                http_client=sync, http_async_client=asynchronous)
            for allowance in (1400, 4096):
                payload = model._get_request_payload(
                    [HumanMessage(content='offline')], max_tokens=allowance)
                assert payload['max_completion_tokens'] == allowance
                assert 'max_tokens' not in payload
                assert 'reasoning_effort' not in payload
                assert 'response_format' not in payload
                assert model._use_responses_api(payload) is False
            assert model.max_retries == 0

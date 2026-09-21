"""Offline provider classification, A2A transport, and retry regression tests."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

from config.errors import ErrorCode, StructuredError
from services.llm_errors import classify_llm_error
from services.a2a_client import A2AClient
from agents.primary.timeline_agent import TimelineAgent
from agents.critics.timeline_critic import TimelineCritic
from agents.orchestrator import PipelineOrchestrator
from models.agent_output import PipelineStatus, AgentScoreResult

pytestmark = pytest.mark.usefixtures("block_network")

FAKE_SECRET = "offline-sensitive-marker"


def sdk_error(status, body=None, headers=None):
    request = httpx.Request("POST", f"https://offline.invalid/?key={FAKE_SECRET}")
    response = httpx.Response(status, request=request, headers=headers)
    error_type = {429: openai.RateLimitError, 401: openai.AuthenticationError,
                  403: openai.PermissionDeniedError}.get(status, openai.APIStatusError)
    return error_type(FAKE_SECRET, response=response, body=body)


def classify(exc):
    return classify_llm_error(exc, provider="gemini", model="gemini-3.6-flash")


@pytest.mark.parametrize("body", [
    {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Request quota exceeded"}},
    {"status": "RESOURCE_EXHAUSTED", "details": [{
        "@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
        ]}]},
    {"type": "insufficient_quota"}, {"code": "credit_balance_exhausted"},
])
def test_quota_exhaustion_is_nonretryable(body):
    error = classify(sdk_error(429, body))
    assert error.code == ErrorCode.LLM_QUOTA_EXCEEDED
    assert error.details["retryable"] is False


@pytest.mark.parametrize("body,headers,delay", [
    ({"status": "RESOURCE_EXHAUSTED", "details": [{
        "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "3.5s"}]}, {}, 3.5),
    ({"details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]}]}, {"Retry-After": "4"}, 4),
    ({}, {"retry-after-ms": "2500"}, 2.5),
    ({"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                   "retryDelay": {"seconds": "2", "nanos": 500000000}}]}, {}, 2.5),
])
def test_transient_rate_limit_preserves_sanitized_delay(body, headers, delay):
    error = classify(sdk_error(429, body, headers))
    assert error.code == ErrorCode.LLM_RATE_LIMITED
    assert error.details["retryable"] is True
    assert error.details["retry_delay"] == delay


@pytest.mark.parametrize("status,expected", [
    (401, ErrorCode.LLM_AUTH_ERROR), (403, ErrorCode.LLM_AUTH_ERROR),
    (500, ErrorCode.LLM_PROVIDER_ERROR), (503, ErrorCode.LLM_PROVIDER_ERROR),
])
def test_status_classification(status, expected):
    assert classify(sdk_error(status)).code == expected


def test_timeout_and_network_classification():
    request = httpx.Request("POST", "https://offline.invalid")
    for exc in (openai.APITimeoutError(request=request), httpx.ReadTimeout(FAKE_SECRET), TimeoutError()):
        assert classify(exc).code == ErrorCode.LLM_TIMEOUT
    assert classify(openai.APIConnectionError(request=request)).code == ErrorCode.LLM_PROVIDER_ERROR
    # Text alone must not classify a programming error as a retryable 429.
    assert classify(RuntimeError("rate_limit")).code == ErrorCode.LLM_PROVIDER_ERROR


@pytest.mark.asyncio
async def test_scorer_preserves_structured_failure(mock_base_scorer):
    mock_base_scorer.score = AsyncMock(side_effect=StructuredError(ErrorCode.LLM_QUOTA_EXCEEDED))
    response = await mock_base_scorer.handle_a2a_request({
        "jsonrpc": "2.0", "id": "offline", "method": "message/send",
        "params": {"message": {"parts": [{"type": "data", "data": {
            "estimate_id": "offline", "agent_name": "timeline", "output": {}, "input": {}
        }}]}}
    })
    with pytest.raises(StructuredError) as caught:
        A2AClient().extract_result_data(response)
    assert caught.value.code == ErrorCode.LLM_QUOTA_EXCEEDED


def test_no_sensitive_provider_data_survives_serialization():
    error = classify(sdk_error(429, {"type": "insufficient_quota", "message": FAKE_SECRET,
                                   "url": f"https://offline.invalid/?key={FAKE_SECRET}"}))
    payload = error.to_dict()
    payload["message"] = FAKE_SECRET
    payload["details"].update(original_error=FAKE_SECRET, raw_content=FAKE_SECRET,
                              url=f"https://offline.invalid/?key={FAKE_SECRET}", retryable=True)
    restored = StructuredError.from_dict(payload)
    encoded = json.dumps(restored.to_dict())
    assert FAKE_SECRET not in encoded
    assert "http" not in encoded.replace("http_status", "")
    assert restored.details["retryable"] is False


def valid_inputs():
    return {
        "clarification_output": {"projectBrief": {"projectType": "kitchen_remodel",
            "scopeSummary": {"totalSqft": 196}, "timeline": {"desiredStart": "2026-10-01"}}},
        "scope_output": {"divisions": [{"divisionCode": "06", "lineItems": [{"item": "Cabinet"}]}]},
        "cost_output": {"subtotals": {"materials": {"low": 100}}},
        "location_output": {},
    }


def planner(llm):
    return TimelineAgent(firestore_service=AsyncMock(), llm_service=llm)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [ErrorCode.LLM_QUOTA_EXCEEDED, ErrorCode.LLM_RATE_LIMITED,
    ErrorCode.LLM_AUTH_ERROR, ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_PROVIDER_ERROR])
async def test_timeline_provider_failure_never_becomes_empty_output(code):
    error = StructuredError(code)
    agent = planner(SimpleNamespace(generate_json=AsyncMock(side_effect=error)))
    with pytest.raises(StructuredError) as raised:
        await agent.run("offline", valid_inputs())
    assert raised.value is error
    agent.firestore.save_agent_output.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, [], {}, {"tasks": []}, {"tasks": [None]},
    {"tasks": [{"name": "Cabinet", "duration_days": None}]},
    {"tasks": [{"name": "Cabinet", "duration_days": 2, "phase": "unknown"}]}])
async def test_timeline_invalid_schema_is_not_insufficient_data(content):
    agent = planner(SimpleNamespace(generate_json=AsyncMock(return_value={"content": content, "tokens_used": 1})))
    with pytest.raises(StructuredError) as raised:
        await agent.run("offline", valid_inputs())
    assert raised.value.code == ErrorCode.LLM_INVALID_RESPONSE


@pytest.mark.asyncio
async def test_missing_inputs_fail_without_llm():
    llm = SimpleNamespace(generate_json=AsyncMock())
    agent = planner(llm)
    with pytest.raises(StructuredError) as raised:
        await agent.run("offline", {})
    assert raised.value.code == ErrorCode.INSUFFICIENT_DATA
    llm.generate_json.assert_not_awaited()
    agent.firestore.save_agent_output.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_error_round_trip_a2a(capsys):
    error = classify(sdk_error(429, {"type": "insufficient_quota"}))
    agent = planner(SimpleNamespace(generate_json=AsyncMock(side_effect=error)))
    response = await agent.handle_a2a_request({"jsonrpc": "2.0", "id": "offline", "method": "message/send",
        "params": {"message": {"parts": [{"type": "data", "data": {
            "estimate_id": "offline", "input": valid_inputs()}}]}}})
    assert response["result"]["status"] == "failed"
    with pytest.raises(StructuredError) as raised:
        A2AClient.extract_result_data(json.loads(json.dumps(response)))
    assert raised.value.to_dict() == error.to_dict()
    assert FAKE_SECRET not in json.dumps(response) + capsys.readouterr().out


def orchestrator():
    obj = PipelineOrchestrator(firestore_service=AsyncMock(), a2a_client=MagicMock())
    obj._call_primary_agent = AsyncMock()
    obj._call_scorer_agent = AsyncMock(return_value=AgentScoreResult(score=100, passed=True))
    obj._call_critic_agent = AsyncMock(return_value={"issues": [], "how_to_fix": []})
    return obj


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [ErrorCode.LLM_QUOTA_EXCEEDED, ErrorCode.LLM_AUTH_ERROR,
    ErrorCode.LLM_TIMEOUT, ErrorCode.LLM_PROVIDER_ERROR, ErrorCode.LLM_INVALID_RESPONSE,
    ErrorCode.INSUFFICIENT_DATA])
async def test_provider_failure_bypasses_scorer_critic_quality_retries(code):
    obj = orchestrator()
    obj._call_primary_agent.side_effect = StructuredError(code)
    state = PipelineStatus()
    success, output = await obj._run_agent_with_validation("offline", "timeline", {}, state)
    assert not success and output["executionError"]["code"] == code
    obj._call_primary_agent.assert_awaited_once()
    obj._call_scorer_agent.assert_not_awaited()
    obj._call_critic_agent.assert_not_awaited()
    assert state.retries["timeline"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("delay,expected", [(2, 2), (120, 1), (None, 1)])
async def test_rate_limit_has_only_one_bounded_retry(monkeypatch, delay, expected):
    from agents import orchestrator as module
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    obj = orchestrator()
    obj._call_primary_agent.side_effect = StructuredError(ErrorCode.LLM_RATE_LIMITED, retry_delay=delay)
    state = PipelineStatus()
    success, output = await obj._run_agent_with_validation("offline", "timeline", {}, state)
    assert not success
    assert obj._call_primary_agent.await_count == expected
    assert output["providerRetries"] == expected - 1
    assert sleep.await_count == expected - 1
    assert state.retries["timeline"] == 0
    obj._call_scorer_agent.assert_not_awaited()
    obj._call_critic_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_normal_quality_retry_is_preserved():
    obj = orchestrator()
    obj._call_primary_agent.return_value = {"tasks": ["offline"]}
    obj._call_scorer_agent.side_effect = [AgentScoreResult(score=35, passed=False), AgentScoreResult(score=95, passed=True)]
    state = PipelineStatus()
    success, _ = await obj._run_agent_with_validation("offline", "timeline", {}, state)
    assert success
    assert obj._call_primary_agent.await_count == 2
    assert obj._call_scorer_agent.await_count == 2
    obj._call_critic_agent.assert_awaited_once()
    assert state.retries["timeline"] == 1


@pytest.mark.asyncio
async def test_persisted_pipeline_failure_is_sanitized_and_specific(monkeypatch):
    from agents import orchestrator as module
    monkeypatch.setattr(module, "AGENT_SEQUENCE", ["timeline"])
    obj = orchestrator()
    obj._call_primary_agent.side_effect = classify(sdk_error(429, {"type": "insufficient_quota"}))
    result = await obj.run_pipeline("offline", {})
    assert result.status == "failed"
    updates = [call.args[1] for call in obj.firestore.update_estimate.await_args_list]
    final = next(update for update in updates if "executionError" in update)
    assert final["executionError"]["code"] == ErrorCode.LLM_QUOTA_EXCEEDED
    assert FAKE_SECRET not in json.dumps(updates, default=str)
    obj._call_critic_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_critic_does_not_swallow_quota_failure():
    critic = TimelineCritic(firestore_service=AsyncMock(), llm_service=SimpleNamespace(
        generate_json=AsyncMock(side_effect=StructuredError(ErrorCode.LLM_QUOTA_EXCEEDED))))
    critic.analyze_output = AsyncMock(return_value={"issues": []})
    with pytest.raises(StructuredError):
        await critic.critique("offline", {}, {}, 35, "offline")


@pytest.mark.asyncio
async def test_llm_service_classification_json_and_cancellation(monkeypatch):
    from services import llm_service as module
    monkeypatch.setattr(module, "settings", SimpleNamespace(llm_model="gemini-3.6-flash",
        llm_provider="gemini", llm_temperature=0.1, llm_client_options={}))
    service = module.LLMService(api_key="offline-placeholder")
    fake = SimpleNamespace(ainvoke=AsyncMock(side_effect=sdk_error(429, {"type": "insufficient_quota"})))
    service._client = fake
    with pytest.raises(StructuredError) as raised:
        await service.generate([])
    assert raised.value.code == ErrorCode.LLM_QUOTA_EXCEEDED
    fake.ainvoke.side_effect = None
    fake.ainvoke.return_value = SimpleNamespace(content="not json", usage_metadata={})
    with pytest.raises(StructuredError) as raised:
        await service.generate_json("offline", "offline")
    assert raised.value.code == ErrorCode.LLM_INVALID_RESPONSE
    fake.ainvoke.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        async with service:
            await service.generate([])
    assert service._client is None

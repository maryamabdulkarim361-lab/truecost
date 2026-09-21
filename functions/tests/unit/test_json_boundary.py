"""Strict offline JSON boundary tests; no provider requests."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import pytest
from services.llm_service import LLMService
from config.errors import StructuredError

pytestmark = pytest.mark.usefixtures('block_network')

@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['{}',' \n{} \t','```json\n{}\n```','```\n{}\n```'])
async def test_valid(text):
    s=LLMService(api_key='offline-fake')
    s.generate_with_system_prompt=AsyncMock(return_value={'content':text,'tokens_used':1})
    assert await s.generate_json('offline','offline') == {'content':{},'tokens_used':1}
    s.generate_with_system_prompt.assert_awaited_once()

@pytest.mark.asyncio
@pytest.mark.parametrize('text,reason', [
    ('','empty_response'),('  \n','empty_response'),(None,'empty_response'),
    ('prose','invalid_json'),('before {}','invalid_json'),('{} after','invalid_json'),
    ('{"tasks":','invalid_json'),('{} {}','invalid_json'),('[]','invalid_json_type'),
    ('"text"','invalid_json_type'),('2','invalid_json_type'),('true','invalid_json_type'),
    ('null','invalid_json_type'),('```json\n{}','invalid_json'),
    ('```json\n{}\n```\n```\n{}\n```','invalid_json'),
    ('```json{} ```','invalid_json'),('{"x":NaN}','invalid_json')])
async def test_reject(text,reason):
    s=LLMService(api_key='offline-fake')
    s.generate_with_system_prompt=AsyncMock(return_value={'content':text,'tokens_used':1})
    with pytest.raises(StructuredError) as e: await s.generate_json('offline','offline')
    assert e.value.details['reason']==reason
    assert e.value.details['field_path']=='response'
    assert 'raw_content' not in json.dumps(e.value.to_dict())

@pytest.mark.asyncio
@pytest.mark.parametrize('finish',['length','stop','content_filter','secret-unknown'])
async def test_completion(finish):
    s=LLMService(api_key='offline-fake')
    s._client=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(
        content='truncated {', usage_metadata={'total_tokens':1},response_metadata={'finish_reason':finish})))
    with pytest.raises(StructuredError) as e: await s.generate_json('offline','offline')
    assert e.value.details.get('completion_status') == (None if finish=='secret-unknown' else finish)
    assert 'truncated' not in json.dumps(e.value.to_dict())

@pytest.mark.asyncio
@pytest.mark.parametrize('content,expected',[({'tasks':[{'name':'task','phase':'finish','duration_days':None,'primary_trade':'carpenter','depends_on':[]}]},'LLM_INVALID_RESPONSE'),({'status':'insufficient_information'},'INSUFFICIENT_DATA')])
async def test_semantics(content,expected):
    from agents.primary.timeline_agent import TimelineAgent
    s=LLMService(api_key='offline-fake')
    s.generate_with_system_prompt=AsyncMock(return_value={'content':json.dumps(content),'tokens_used':1})
    agent=TimelineAgent(firestore_service=AsyncMock(),llm_service=s)
    with pytest.raises(StructuredError) as e:
        await agent._generate_task_specs_with_llm('offline','kitchen_remodel',196,{},{},{},None)
    assert e.value.code==expected

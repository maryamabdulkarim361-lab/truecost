"""Synthetic markers only; no provider or credential access."""
import json
import pytest
import structlog
import httpx
from config.safe_logging import configure_logging, sanitize_event, safe_error_text
from config.errors import StructuredError, ErrorCode

MARKER='SYNTHETIC_SECRET_MARKER'

@pytest.fixture(autouse=True)
def logging_config(block_network):
    old=structlog.get_config().copy()
    configure_logging()
    yield
    structlog.configure(**old)


def test_log_drops_exception_body_headers_and_narrative(capsys):
    logger=structlog.get_logger()
    try:
        raise RuntimeError(MARKER)
    except Exception as error:
        logger.exception('provider_failed',error=error,headers={'Authorization':MARKER},body=MARKER,
                         prompt=MARKER,url='https://example.com?key='+MARKER,request_id='12345678-1234-1234-1234-123456789012',
                         code=ErrorCode.LLM_PROVIDER_ERROR,http_status=503,reason='provider_http_error',failure_stage='provider_request')
    output=capsys.readouterr().out
    assert MARKER not in output and 'Traceback' not in output
    data=json.loads(output)
    assert data['http_status']==503 and data['reason']=='provider_http_error'
    assert data['request_id']=='12345678-1234-1234-1234-123456789012'

@pytest.mark.parametrize('error,expected',[(RuntimeError(MARKER),'INTERNAL_ERROR'),(TimeoutError(MARKER),'TIMEOUT'),(httpx.ConnectError(MARKER),'NETWORK_ERROR'),(StructuredError(ErrorCode.LLM_QUOTA_EXCEEDED),ErrorCode.LLM_QUOTA_EXCEEDED)])
def test_exception_summary(error,expected):
    assert safe_error_text(error)==expected


def test_invalid_metadata_is_not_serialized():
    result=sanitize_event(None,None,{'event':MARKER,'code':{'body':MARKER},'failure_stage':MARKER,'reason':MARKER,'input':MARKER,'exception':MARKER,'estimate_id':MARKER})
    assert MARKER not in json.dumps(result)


def test_visual_logger_never_prints_agent_content(capsys):
    from utils.agent_logger import log_agent_output,log_input_context,log_agent_error
    log_agent_output('timeline','est-test',{'body':MARKER})
    log_input_context('timeline','est-test',{'prompt':MARKER})
    log_agent_error('timeline','est-test',MARKER)
    assert MARKER not in capsys.readouterr().out

@pytest.mark.asyncio
async def test_generic_agent_error_response_is_safe(mock_base_agent,sample_a2a_request,capsys):
    async def fail(*args,**kwargs): raise RuntimeError(MARKER)
    mock_base_agent.run=fail
    result=await mock_base_agent.handle_a2a_request(sample_a2a_request)
    assert result['result']['status']=='failed'
    assert MARKER not in json.dumps(result)
    assert MARKER not in capsys.readouterr().out

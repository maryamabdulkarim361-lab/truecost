"""Application log allowlist and exception summaries; never serialize raw errors."""
import math
import re
import structlog
import httpx
from config.errors import ErrorCode, StructuredError, TrueCostError

CODES = {v for k, v in vars(ErrorCode).items() if k.isupper() and isinstance(v, str)}
AGENTS = {'location','scope','code_compliance','cost','risk','timeline','final'}


def safe_error_text(error):
    # Preserve this fixed application diagnostic without copying arbitrary text.
    if isinstance(error, TrueCostError) and error.message == 'Stale or abandoned Cost attempt':
        return 'Stale or abandoned Cost attempt'
    code = getattr(error, 'code', None)
    if isinstance(code, str) and code in CODES:
        return code
    status = getattr(error, 'status_code', None)
    if status is None:
        status = getattr(getattr(error, 'response', None), 'status_code', None)
    if type(status) is int and 400 <= status <= 599:
        return f'HTTP_{status}'
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return 'TIMEOUT'
    if isinstance(error, (ConnectionError, httpx.NetworkError)):
        return 'NETWORK_ERROR'
    return 'INTERNAL_ERROR'


def sanitize_event(_logger, _method, event):
    label = event.get('event')
    result = {'event': label if isinstance(label,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,95}',label) else 'operation'}
    for key in ('estimate_id','request_id','task_id','thread_id'):
        value=event.get(key)
        if isinstance(value,str) and re.fullmatch(r'(?:[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}|est-[a-z0-9-]{1,72})',value):
            result[key]=value
    for key in ('agent','agent_name','target_agent','current_agent','failed_agent','scorer','critic'):
        value=event.get(key)
        if isinstance(value,str) and value.removesuffix('_scorer').removesuffix('_critic') in AGENTS:
            result[key]=value
    for key in ('code','error_code'):
        value=event.get(key)
        if isinstance(value,str) and value in CODES:
            result[key]=value
    for key in ('score','previous_score','duration_ms','duration_seconds','retry_attempt','retry_number','tokens_used','total_tokens','agent_count','http_status','status_code','timeout','progress','attempt','items_estimated','total_project_hours','total_material_cost'):
        value=event.get(key)
        if type(value) in (int,float) and math.isfinite(value): result[key]=value
    if type(event.get('passed')) is bool: result['passed']=event['passed']
    summary=event.get('error')
    if isinstance(summary,str):
        if summary in CODES or summary in ('INTERNAL_ERROR','TIMEOUT','NETWORK_ERROR'):
            result['error_code']=summary
        elif re.fullmatch(r'HTTP_[45][0-9]{2}',summary):
            result['http_status']=int(summary[5:])
        elif summary=='Stale or abandoned Cost attempt':
            result['reason']='stale_attempt'
    # Reuse the strict provider diagnostic allowlist; unknown fields are dropped.
    diagnostic=StructuredError(ErrorCode.LLM_PROVIDER_ERROR, **{k:event.get(k) for k in
        ('provider','model','http_status','retry_delay','reason','field_path','failure_stage','completion_status')}).details
    diagnostic.pop('retryable',None)
    result.update(diagnostic)
    # Exception objects/messages, output, input, feedback, URLs, headers, and
    # exc_info/stack_info never reach the renderer.
    return result


def configure_logging():
    structlog.configure(processors=[sanitize_event,structlog.processors.JSONRenderer()],cache_logger_on_first_use=False)

"""Classify provider errors without copying their messages or response data."""

import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx
import openai

from config.errors import ErrorCode, StructuredError


def _seconds(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and 0 <= number <= 86400 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(exc, details):
    delays = []
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    for key, scale in (("retry-after-ms", 0.001), ("retry-after", 1)):
        value = headers.get(key)
        if value is None:
            continue
        try:
            seconds = float(value) * scale
        except (ValueError, TypeError):
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                continue
        number = _seconds(seconds)
        if number is not None:
            delays.append(number)
    for detail in details:
        if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith("google.rpc.RetryInfo"):
            continue
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.endswith("s"):
            number = _seconds(delay[:-1])
        elif isinstance(delay, dict):
            try:
                number = _seconds(float(delay.get("seconds", 0)) + float(delay.get("nanos", 0)) / 1e9)
            except (TypeError, ValueError):
                number = None
        else:
            number = None
        if number is not None:
            delays.append(number)
    return max(delays) if delays else None


def _classify_one(exc, *, provider, model):
    if isinstance(exc, StructuredError):
        return exc
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    body = getattr(exc, "body", {})
    body = body if isinstance(body, dict) else {}
    if isinstance(body.get("error"), dict):
        body = body["error"]
    details = body.get("details", [])
    details = details if isinstance(details, list) else []
    codes = {str(body.get(k, "")).lower() for k in ("code", "type", "status")}
    code = ErrorCode.LLM_PROVIDER_ERROR
    if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        code = ErrorCode.LLM_TIMEOUT
    elif isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)) or status in (401, 403):
        code = ErrorCode.LLM_AUTH_ERROR
    elif isinstance(exc, openai.RateLimitError) or status == 429:
        hard_quota = bool(codes & {"insufficient_quota", "credit_balance_exhausted",
                                  "billing_hard_limit_reached", "quota_exceeded", "quota_exhausted"})
        quota_details = [d for d in details if isinstance(d, dict)
                         and str(d.get("@type", "")).endswith("google.rpc.QuotaFailure")]
        if quota_details:
            violations = [v for d in quota_details
                          for v in (d.get("violations") if isinstance(d.get("violations"), list) else [])
                          if isinstance(v, dict)]
            # Explicit short-window quotas can recover after a delay. Unknown or
            # daily quota windows fail closed instead of spending more requests.
            short_window = bool(violations) and all(
                any(s in str(v.get("quotaId", "")).lower() for s in ("perminute", "persecond"))
                for v in violations
            )
            hard_quota = hard_quota or not short_window
        elif "resource_exhausted" in codes:
            # Supplement typed/status evidence for compatible endpoints that
            # omit QuotaFailure. The upstream message is never retained.
            message = str(body.get("message", "")).lower()
            hard_quota = hard_quota or "quota exceeded" in message or "quota exhausted" in message
        code = ErrorCode.LLM_QUOTA_EXCEEDED if hard_quota else ErrorCode.LLM_RATE_LIMITED
    elif isinstance(exc, openai.APIResponseValidationError):
        code = ErrorCode.LLM_INVALID_RESPONSE
    elif codes & {"context_length_exceeded", "max_context_length_exceeded"}:
        code = ErrorCode.LLM_CONTEXT_TOO_LONG
    stage = "provider_response" if isinstance(exc, openai.APIResponseValidationError) else None
    reason = None
    if code == ErrorCode.LLM_PROVIDER_ERROR:
        if isinstance(exc, (openai.APIConnectionError, httpx.TransportError, ConnectionError)):
            reason = "transport_error"
        elif type(status) is int and 400 <= status <= 599:
            reason = "provider_http_error"
        else:
            reason = "unclassified_error"
    return StructuredError(code, reason=reason, failure_stage=stage, provider=provider, model=model, http_status=status,
                           retry_delay=_retry_delay(exc, details))


MAX_CHAIN_DEPTH = 4
MAX_CHAIN_NODES = 8


def classify_llm_error(exc, *, provider, model, failure_stage="unknown"):
    """Inspect bounded cause/context links; never retain exceptions or messages."""
    if isinstance(exc, StructuredError):
        return exc
    queue = [(exc, 0)]
    seen = set()
    candidates = []
    while queue and len(seen) < MAX_CHAIN_NODES:
        current, depth = queue.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        # The SDK wraps request-hook failures as connection errors. Preserve the
        # opt-in local ceiling instead of misreporting it as provider downtime.
        if (isinstance(current, StructuredError)
                and current.details.get('reason') == 'validation_budget_blocked'):
            return StructuredError.from_dict(current.to_dict())
        candidate = _classify_one(current, provider=provider, model=model)
        candidates.append(candidate)
        if depth < MAX_CHAIN_DEPTH:
            for link in (current.__cause__, current.__context__):
                if isinstance(link, BaseException) and id(link) not in seen:
                    queue.append((link, depth + 1))
    # Recognized categories beat generic wrappers; otherwise prefer HTTP/transport
    # evidence over an unclassified wrapper. Nearest evidence wins ties.
    chosen = next((c for c in candidates if c.code != ErrorCode.LLM_PROVIDER_ERROR), None)
    if chosen is None:
        chosen = next((c for c in candidates if c.details.get("reason") in
                       ("transport_error", "provider_http_error")), candidates[0])
    details = dict(chosen.details)
    details.pop("retryable", None)
    details.setdefault("failure_stage", failure_stage)
    if chosen.code == ErrorCode.LLM_PROVIDER_ERROR and details.get("reason") == "unclassified_error":
        if failure_stage == "response_processing":
            details["reason"] = "response_processing_error"
        elif failure_stage == "client_initialization" and isinstance(exc, RuntimeError):
            details["reason"] = "client_lifecycle_error"
    if isinstance(exc, openai.APIResponseValidationError):
        details["failure_stage"] = "provider_response"
    return StructuredError(chosen.code, **details)

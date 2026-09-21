"""TrueCost error handling.

Custom exceptions and error codes for the deep agent pipeline.
"""

from typing import Optional, Dict, Any
import math
import re


# Error Codes
class ErrorCode:
    """Error code constants."""
    
    # Validation Errors (1xxx)
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD = "INVALID_FIELD"
    
    # Agent Errors (2xxx)
    AGENT_TIMEOUT = "AGENT_TIMEOUT"
    AGENT_FAILED = "AGENT_FAILED"
    AGENT_VALIDATION_FAILED = "AGENT_VALIDATION_FAILED"
    AGENT_MAX_RETRIES_EXCEEDED = "AGENT_MAX_RETRIES_EXCEEDED"
    
    # Pipeline Errors (3xxx)
    PIPELINE_FAILED = "PIPELINE_FAILED"
    PIPELINE_TIMEOUT = "PIPELINE_TIMEOUT"
    PIPELINE_INVALID_STATE = "PIPELINE_INVALID_STATE"
    
    # A2A Protocol Errors (4xxx)
    A2A_CONNECTION_ERROR = "A2A_CONNECTION_ERROR"
    A2A_TIMEOUT = "A2A_TIMEOUT"
    A2A_INVALID_RESPONSE = "A2A_INVALID_RESPONSE"
    A2A_METHOD_NOT_FOUND = "A2A_METHOD_NOT_FOUND"
    
    # Firestore Errors (5xxx)
    FIRESTORE_ERROR = "FIRESTORE_ERROR"
    ESTIMATE_NOT_FOUND = "ESTIMATE_NOT_FOUND"
    FIRESTORE_WRITE_FAILED = "FIRESTORE_WRITE_FAILED"
    
    # LLM Errors (6xxx)
    LLM_ERROR = "LLM_ERROR"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"
    LLM_CONTEXT_TOO_LONG = "LLM_CONTEXT_TOO_LONG"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    LLM_QUOTA_EXCEEDED = "LLM_QUOTA_EXCEEDED"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_AUTH_ERROR = "LLM_AUTH_ERROR"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"
    
    # External Service Errors (7xxx)
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"
    COST_DATA_ERROR = "COST_DATA_ERROR"
    MONTE_CARLO_ERROR = "MONTE_CARLO_ERROR"


class TrueCostError(Exception):
    """Base exception for TrueCost errors.
    
    Provides structured error information for API responses.
    
    Attributes:
        code: Error code from ErrorCode constants
        message: Human-readable error message
        details: Additional error context
    """
    
    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None
    ):
        """Initialize TrueCostError.
        
        Args:
            code: Error code from ErrorCode constants
            message: Human-readable error message
            details: Additional error context
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for API response.
        
        Returns:
            Dictionary with code, message, and details.
        """
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details
        }
    
    def __repr__(self) -> str:
        return f"TrueCostError(code={self.code!r}, message={self.message!r})"


class StructuredError(TrueCostError):
    """Allowlisted execution failure safe to send through A2A and persist.

    Never accept upstream exception messages, bodies, headers, or URLs here.
    """

    MESSAGES = {
        ErrorCode.INSUFFICIENT_DATA: "Required project inputs are missing or unusable",
        ErrorCode.LLM_QUOTA_EXCEEDED: "LLM provider quota exhausted",
        ErrorCode.LLM_RATE_LIMITED: "LLM provider rate limit exceeded",
        ErrorCode.LLM_AUTH_ERROR: "LLM provider authentication or authorization failed",
        ErrorCode.LLM_TIMEOUT: "LLM provider request timed out",
        ErrorCode.LLM_PROVIDER_ERROR: "LLM provider request failed",
        ErrorCode.LLM_INVALID_RESPONSE: "LLM response is invalid",
        ErrorCode.LLM_CONTEXT_TOO_LONG: "Input too long for model context",
    }

    REASONS = frozenset({
        "empty_response", "invalid_json_type", "invalid_json", "invalid_type", "tasks_missing", "tasks_empty", "missing",
        "null", "out_of_range", "invalid", "blank", "duplicate",
        "unknown_task", "self_dependency", "insufficient_scheduling_information",
        "conflicting_state", "transport_error", "provider_http_error", "validation_budget_blocked",
        "client_lifecycle_error", "response_processing_error", "unclassified_error",
    })

    STAGES = frozenset({"client_initialization", "provider_request", "provider_response",
                        "response_processing", "timeline_prompt_construction",
                        "timeline_processing", "unknown"})

    def __init__(self, code, *, provider=None, model=None, http_status=None, retry_delay=None,
                 reason=None, field_path=None, failure_stage=None, completion_status=None):
        if code not in self.MESSAGES:
            code = ErrorCode.LLM_PROVIDER_ERROR
        details = {"retryable": code == ErrorCode.LLM_RATE_LIMITED}
        if provider in ("gemini", "openai"):
            details["provider"] = provider
        if isinstance(model, str) and re.fullmatch(r"(?:gemini-|gpt-|o[134](?:-|$))[a-zA-Z0-9_.:-]*", model):
            details["model"] = model
        if type(http_status) is int and 400 <= http_status <= 599:
            details["http_status"] = http_status
        if (type(retry_delay) in (int, float) and math.isfinite(retry_delay)
                and 0 <= retry_delay <= 86400):
            details["retry_delay"] = float(retry_delay)
        if completion_status in ("stop", "length", "content_filter", "tool_calls", "function_call"):
            details["completion_status"] = completion_status
        if isinstance(failure_stage, str) and failure_stage in self.STAGES:
            details["failure_stage"] = failure_stage
        if isinstance(reason, str) and reason in self.REASONS:
            details["reason"] = reason
        if isinstance(field_path, str) and re.fullmatch(
            r"(?:response|status|tasks(?:\[[0-9]{1,6}\](?:\.(?:name|phase|duration_days|primary_trade|depends_on))?)?)",
            field_path,
        ):
            details["field_path"] = field_path
        super().__init__(code, self.MESSAGES[code], details)

    @classmethod
    def from_dict(cls, payload):
        details = payload.get("details")
        details = details if isinstance(details, dict) else {}
        return cls(payload.get("code"), **{k: details.get(k) for k in
                   ("provider", "model", "http_status", "retry_delay", "reason", "field_path", "failure_stage", "completion_status")})


class ValidationError(TrueCostError):
    """Validation-specific error."""
    
    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Dict] = None):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details={**(details or {}), "field": field} if field else details
        )


class AgentError(TrueCostError):
    """Agent-specific error."""
    
    def __init__(
        self,
        code: str,
        message: str,
        agent_name: str,
        details: Optional[Dict] = None
    ):
        super().__init__(
            code=code,
            message=message,
            details={**(details or {}), "agent_name": agent_name}
        )
        self.agent_name = agent_name


class PipelineError(TrueCostError):
    """Pipeline-specific error."""
    
    def __init__(
        self,
        code: str,
        message: str,
        estimate_id: str,
        current_agent: Optional[str] = None,
        details: Optional[Dict] = None
    ):
        super().__init__(
            code=code,
            message=message,
            details={
                **(details or {}),
                "estimate_id": estimate_id,
                "current_agent": current_agent
            }
        )
        self.estimate_id = estimate_id
        self.current_agent = current_agent


class A2AError(TrueCostError):
    """A2A protocol-specific error."""
    
    def __init__(
        self,
        code: str,
        message: str,
        target_agent: str,
        details: Optional[Dict] = None
    ):
        super().__init__(
            code=code,
            message=message,
            details={**(details or {}), "target_agent": target_agent}
        )
        self.target_agent = target_agent


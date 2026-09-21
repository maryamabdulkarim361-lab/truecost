"""Pipeline diagnostics: metadata only, never output/prompt/error bodies."""
from typing import Dict, Any
import structlog
from config.safe_logging import CODES
logger = structlog.get_logger()

def _error_code(error):
    candidate=error.split(':',1)[0] if isinstance(error,str) else None
    return candidate if candidate in CODES else 'INTERNAL_ERROR'


def log_pipeline_start(estimate_id: str, agent_count: int) -> None:
    logger.info('log_pipeline_start', estimate_id=estimate_id, agent_count=agent_count)

def log_pipeline_complete(
    estimate_id: str,
    completed_agents: list,
    duration_ms: int,
    total_tokens: int
) -> None:
    logger.info('log_pipeline_complete', estimate_id=estimate_id, duration_ms=duration_ms, total_tokens=total_tokens)

def log_pipeline_failed(
    estimate_id: str,
    failed_agent: str,
    error: str,
    completed_agents: list
) -> None:
    logger.error('log_pipeline_failed', estimate_id=estimate_id, failed_agent=failed_agent, error_code=_error_code(error))

def log_agent_start(agent_name: str, estimate_id: str, retry_attempt: int = 0) -> None:
    logger.info('log_agent_start', agent_name=agent_name, estimate_id=estimate_id, retry_attempt=retry_attempt)

def log_agent_output(
    agent_name: str,
    estimate_id: str,
    output: Dict[str, Any],
    duration_ms: int = 0,
    tokens_used: int = 0,
    truncate: bool = True
) -> None:
    logger.info('log_agent_output', agent_name=agent_name, estimate_id=estimate_id, duration_ms=duration_ms, tokens_used=tokens_used)

def log_scorer_result(
    agent_name: str,
    estimate_id: str,
    score: int,
    passed: bool,
    breakdown: list,
    feedback: str = ""
) -> None:
    logger.info('log_scorer_result', agent_name=agent_name, estimate_id=estimate_id, score=score, passed=passed)

def log_critic_feedback(
    agent_name: str,
    estimate_id: str,
    feedback: Dict[str, Any]
) -> None:
    logger.info('log_critic_feedback', agent_name=agent_name, estimate_id=estimate_id)

def log_agent_error(
    agent_name: str,
    estimate_id: str,
    error: str,
    retry_attempt: int = 0
) -> None:
    logger.error('log_agent_error', agent_name=agent_name, estimate_id=estimate_id, retry_attempt=retry_attempt, error_code=_error_code(error))

def log_agent_retry(
    agent_name: str,
    estimate_id: str,
    retry_number: int,
    previous_score: int
) -> None:
    logger.info('log_agent_retry', agent_name=agent_name, estimate_id=estimate_id, retry_number=retry_number, previous_score=previous_score)

def log_input_context(
    agent_name: str,
    estimate_id: str,
    input_data: Dict[str, Any],
    truncate: bool = True
) -> None:
    logger.info('log_input_context', agent_name=agent_name, estimate_id=estimate_id)

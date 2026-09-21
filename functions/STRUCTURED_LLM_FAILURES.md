# Structured LLM failures

LLMService classifies SDK exceptions, HTTP status, and structured provider
details into sanitized errors. Raw upstream messages, bodies, headers, and
URLs are excluded from the A2A/persisted error contract.

Timeline raises execution errors instead of saving empty schedules. Missing
required inputs are INSUFFICIENT_DATA; invalid model output is
LLM_INVALID_RESPONSE. Neither is scored as a successful schedule.

SDK retries are disabled. The orchestrator permits at most one additional
primary request per agent for LLM_RATE_LIMITED with an explicit retry delay
of at most 15 seconds, waiting at least one second. Longer or absent delays
stop the pipeline. Quota exhaustion, authentication failures, and other
execution errors bypass quality retries, scorers, and critics. Successful
low-quality outputs retain their existing quality-improvement policy.

Short-window Gemini QuotaFailure identifiers can indicate a transient rate
limit; daily or unknown quota windows are treated conservatively as quota
exhaustion. RetryInfo and Retry-After are honored within the bounded policy.

## Follow-up scope

Other primary-agent fallback policies are unchanged. Location and Scope
analysis, Scope searchable names, Cost complexity/analysis, Risk analysis,
and Final narrative paths should be audited separately for fallback provenance
and swallowed structured failures. This change does not claim that every
optional LLM failure in those agents terminates the pipeline.

No deterministic Timeline scheduling or live provider validation is included.

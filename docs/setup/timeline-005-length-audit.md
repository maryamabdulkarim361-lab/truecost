# Timeline-005 length audit — offline fix

**est-local-timeline-005 is consumed. Never rerun it.** Its single generation
returned non-parseable JSON with `finish_reason=length`; no Timeline output was
persisted. This is not evidence of quota exhaustion. No live request was made
for this audit.

## Limit and evidence

`TimelineAgent._generate_task_specs_with_llm` previously passed `max_tokens=1400`.
This is Timeline-specific, not a global model limit. `LLMService.generate_json`
passes it through `generate_with_system_prompt` and `generate` to `ainvoke`.
Installed LangChain `ChatOpenAI._get_request_payload` renames it to
`max_completion_tokens=1400`; the offline serialization test confirms the exact
wire field. There is no intervening configured larger limit, repair generation,
or reasoning-effort override. SDK retries remain zero.

Gemini mode fixes the Google OpenAI-compatible endpoint and disables Responses
API routing. JSON generation currently adds text-only JSON instructions; it does
not set native `response_format` or a provider schema. Local parsing and Timeline
validation enforce the authoritative contract afterward.

The installed OpenAI SDK describes `max_completion_tokens` as including visible
and reasoning tokens. That is the client interface contract, **not a measured
Gemini token split**. No separate thinking/visible-output usage was retained in
the supplied Timeline-005 evidence; logs normalize total usage only. No claim is
made that all 1,400 tokens were visible JSON or that a specific thinking budget
caused the failure. No provider documentation/API lookup was performed.

The firm finding is a limit-ended, invalid-JSON response under an explicitly
small completion allowance. Exact truncation position, generated task count and
provider-side accounting are not recoverable from the retained diagnostics.
Increasing the allowance addresses this deterministic local constraint; it does
not guarantee the next response will be valid or complete.

## Sizing and smallest fix

The selected Scope fixture contains seven divisions and 15 line items. The
existing separate six-task Timeline fixture needs 788 characters in default
JSON formatting or 1,116 with indentation, projecting only the five required
fields (name, phase, duration_days, primary_trade, depends_on). These fixture
values were not changed or presented as live model output.

Using 3–4 characters/token only as a rough sizing heuristic, a 20–30 task response
of comparable structure is about 930–1,860 visible tokens when indented, before
longer names, multiple dependencies or optional notes. This is an estimate, not
a Gemini tokenizer measurement or a required task-count constraint. Roughly
2,000–3,000 visible tokens is a more practical planning allowance for this shape.
There is no provable universal minimum: the contract does not cap task count,
name/notes length or thinking allocation.

The scoped change is **4,096 completion tokens**, a finite allowance with
headroom above that visible-output estimate. No task, duration, dependency,
phase, prompt, provider choice, reasoning setting or validation rule changed.
Optional `notes` are not essential to the contract and can consume tokens, but
there is no evidence they caused Timeline-005; no speculative prompt rewrite
was made. The five required fields are already a compact schedule contract.

## Validation and next gate

166 relevant offline tests passed: Timeline contract (28), JSON boundary (27),
LLM service (9), lifecycle (30), provider configuration (11), structured failure
handling (42) and Cost fencing/budget (19). New cases verify the one-generation
4,096 allowance, exact installed-client payload mapping for both old/new limits,
and length/invalid-JSON propagation with one invocation and no output save.
Existing provider failures still bypass quality retries. Python compilation and
git diff whitespace checks passed. No external network or provider call occurred.

Recommend **est-local-timeline-006** as a fresh candidate only. Its absence has
not been checked because this was an offline audit; no document or runner was
created for it. Before any future execution, refresh the private server to load
the new limit, verify the new ID is unused and current Gemini configuration, and
obtain explicit one-generation authorization. Do not reuse Timeline-005 or
silently increase this allowance again after a failure.

# Pre-live validation status — autonomous zero-provider run

This is local implementation and production preparation, **not deployment approval**. No provider requests, tokens issued, queues, scheduler jobs, IAM changes or deployments were used to validate this work.

## Execution modes and entry points

- **Local development:** existing standalone Python and synchronous non-durable estimates remain supported. Services were not restarted. Existing local Node OpenAI disable configuration was not read or changed.
- **Local durable validation:** injected Firestore emulator clients, fake signed-identity verifier results, fake Cloud Tasks HTTP sessions and fake provider results exercise the real service/persistence boundaries. The seven-stage E2E includes a Scope quality retry and actual Final mapping. This does not validate live agent reasoning.
- **Production preparation:** `PIPELINE_EXECUTION_MODE=durable` is required for managed/production start traffic. Public `start_deep_pipeline` routes to strict clarification validation, verified Firebase identity, project owner/editor authorization, stable idempotency and atomic job/outbox creation, then HTTP 202. No inline agent execution. The old Node forwarding path forwards production 202 responses without creating competing legacy progress writes. Legacy Node progress mutation is disabled in production.
- **MVP live validation:** Timeline-005, isolated Final and TEST-008 remain unexecuted and require a separate approved plan and refreshed runtime configuration.
- **Production deployment:** not authorized or validated. Do not provision from this document alone.

## Production configuration and authentication

Required non-secret names:

| Variable | Meaning |
| --- | --- |
| `PIPELINE_EXECUTION_MODE` | Must be `durable` for production |
| `DURABLE_QUEUE_PATH` | Explicit `projects/.../locations/.../queues/...`; project must agree with Firebase |
| `DURABLE_WORKER_URL` | Exact HTTPS worker target and OIDC audience |
| `DURABLE_DISPATCH_SERVICE_ACCOUNT` | Queue task OIDC identity |
| `DURABLE_WORKER_INVOKER` | Must match dispatch service account |
| `DURABLE_SCANNER_URL`, `DURABLE_SCANNER_INVOKER` | Exact audience and service identity for scheduled bounded pages |
| `PRICING_SERVICE_URL` | Exact HTTPS `comparePricesService` URL and token audience, on both Python and Node |
| `PRICING_SERVICE_INVOKER` | Node allowlisted Python worker service-account email |
| `READINESS_URL` | Exact private readiness audience; scanner identity is permitted |

Existing production project, web origin, Python URL, provider/model and emulator-rejection configuration remains required. No production localhost/default-development routing is accepted. Credential values must be provisioned separately and never copied from local files. The readiness endpoint checks selected credential **presence only** and does not validate provider quota or authentication.

`durable_worker`, `durable_scanner`, `production_readiness` and Node `comparePricesService` are declared private. Application verification additionally requires Google-signed OIDC, exact audience, allowlisted verified email, Google issuer and a subject. Firebase user ID tokens are not service identities. Offline tests replace the cryptographic/network verification boundary; deployed certificate verification and platform IAM have not been tested.

The Cloud Tasks REST adapter uses already installed Python google-auth/requests support. No Cloud Tasks SDK addition or resource creation is needed for code preparation. Node declares its already installed `google-auth-library` version 9.15.1 as a direct dependency; no package was installed/upgraded. The adapter uses deterministic names, identifiers-only base64 JSON, explicit OIDC audience, bounded HTTP, no redirect following, no automatic request retry, and treats HTTP 409 task-exists as idempotent acceptance. Errors are fixed categories, never raw response bodies. Failed enqueue leaves its outbox pending.

Future IAM must grant enqueue only to the dispatcher, permit Cloud Tasks to issue the configured service-account identity, allow only that identity to invoke the worker, only the scheduler identity to invoke scanner/readiness, and only the worker identity to invoke pricing. Exact platform roles and resource bindings require a separate reviewed cloud plan. No user token is used for service-to-service pricing.

## Authoritative persistence and frontend

Only accepted durable checkpoints promote outputs; scorer/critic identity and Cost inner fencing remain composed with the outer fence. Root `pipelineStatus` is a projection derived from the authoritative job in the same atomic commit, including canonical stage ordering, progress and safe terminal code. Frontend keeps the existing Firestore subscription, handles accepted/queued states and uses stable request identifiers. Recreated identical input with no identifier gets a canonical SHA-256 project/input key; changed input with an explicitly reused identifier is rejected as a conflict. A deliberate new estimate for unchanged inputs needs a new explicit identifier; automatic retry does not create one.

Approved money is unchanged: base **27,942.71**, contingency **3,514.03**, finalEstimate/totalCost **31,456.74**, P50 **29,283.60**, P80 **32,797.63**, P90 **35,067.77**. No P80-as-total substitution.

The 400-row Cost ledger cap is an **application atomic-promotion safety budget**, with room for other writes; it is not a claim that Firestore's universal limit is exactly 400. More rows would require a reviewed versioned-ledger/pointer schema or another atomic publication strategy. Multi-batch writes to the current authoritative collection would expose mixed state, so the cap remains fail closed. No mathematics changed.

Concurrent independent jobs targeting one project's shared display status still need an explicit product policy for which estimate is active. Each estimate/job remains fenced independently; a project display projection is not authorization or job authority. Durable-aware cancellation/deletion/retention also remains a separate controlled design, not a destructive automatic repair.

## Recovery and operator-visible evidence

| Condition | Safe behavior / operator action |
| --- | --- |
| Pending outbox or queue unavailable | Keep intent pending; bounded scanner retry, fixed error counts |
| Lost enqueue acknowledgment / duplicate task | Deterministic task identity; task-exists acceptance; lease gate rejects duplicate execution |
| Worker crash or expired lease | Scanner reopens bounded delivery attempt with new generation; old writes rejected |
| Scanner crash | Retain/revisit cursor page; deterministic republish safe; scheduler must store cursor externally |
| Quota/auth/invalid response | Structured terminal category; no quality critic amplification |
| Safe transient rate limit | Existing single coordinated retry and safe delay policy unchanged |
| Final acknowledgement loss | Reread terminal job; duplicate delivery cannot alter accepted monetary root |
| Pricing unavailable | Existing optional enrichment fallback and Cost budgets remain in force |
| Ledger above cap | Fail closed; operator reviews input/limit, never partial authoritative publication |

Use job status/revision/generation, dispatch intent, checkpoints and sanitized error category. Do not log tokens, provider bodies or project narratives in operational error reports. Automatic destructive reset or repair is not provided. Scanner cursor durability, queue retry settings, backlog alerting, retention and restore drills remain cloud operational gates.

## PDF and readiness

Local WeasyPrint 61.2 / pydyf 0.10.0 binary and monetary/HTML tests pass. Managed Python 3.12 Functions packaging still has **no proven Pango, GLib/GObject, Cairo, HarfBuzz, Fontconfig and font installation guarantee**. Local macOS dylibs do not establish Linux runtime compatibility. Do not deploy until the intended image can import and render the approved fixture with correct fonts, money and Storage signing/upload permissions. No engine replacement or speculative container architecture was introduced.

`/health` remains standalone liveness. New private `production_readiness` performs configuration, dispatcher identity agreement, credential presence and native PDF import checks only. It does not call providers, Firestore or queues and therefore does not prove connectivity or production rendering. No health record was created.

## Remaining gates

1. Human code/security review of the new HTTP, OIDC, REST and pricing boundaries.
2. Resolve Storage emulator setup timeout and rerun all 11 Storage rule tests; do not infer pass from Firestore rule results. Blueprint/report bearer URLs remain a separate issuance/expiry/revocation policy; Storage rules do not revoke already issued bearer links.
3. Validate managed native PDF packaging and actual private service/queue delivery in a separately approved cloud environment.
4. Review strict production clarification schema against the live clarification producer; invalid legacy/minimal input now fails closed instead of reaching agents.
5. Configure scanner scheduling/cursor retention, queue policy, least-privilege IAM, service accounts and operational recovery with explicit human/cloud authorization.
6. Only then authorize the smallest necessary live provider validation. No quota probe or live run occurred here.

## Validation record

- Python: 480 distinct tests passed (477-test combined suite plus three additional HTTP/lifecycle boundary cases). The first combined run exposed an existing module-settings fixture leak; the fixture now binds the fake LLM settings explicitly and the combined rerun passed. The network guard stopped the initial accidental ADC-discovery attempt before connection; no provider request was made.
- Firestore: 51 distinct emulator tests passed, including production-shaped HTTP/outbox/REST/worker E2E and failed enqueue recovery. All use isolated demo data.
- Firestore rules: 30 passed. Storage rules: 11 did not execute because setup timed out on the running local suite. Initial default isolated ports were unavailable; local test port overrides were added, but the subsequent setup still timed out. No restart or rule deployment was performed.
- Node: 129 passed, including private identity, callable authorization, OpenAI disable, pricing and production forwarding tests.
- Frontend: 20 passed. TypeScript project checks and Vite bundle completed; bundle used `envDir:false` and a temporary output directory, so this is not a configured deployable production artifact.
- Local PDF: all 35 generator tests and 18 monetary PDF tests passed within the Python count; none skipped for missing native libraries.
- **710 distinct passing tests**, with 11 Storage cases blocked by suite setup (not counted as passes). Relevant changed Python files compile. Node TypeScript emits to a temporary directory; existing compiled files/services were left untouched. Git diff whitespace check passes.

No source changes were made to LLM provider behavior, Cost/Risk mathematics, agent prompts, timeout policy, Node OpenAI disable semantics, or authoritative monetary mappings. No external provider calls, real Cloud Tasks calls, live estimates, credentials, IAM, deployments, commits or pushes occurred. Existing unrelated worktree changes remain intact.

## Repository files changed in this run

- `collabcanvas/functions/package-lock.json`
- `collabcanvas/functions/package.json`
- `collabcanvas/functions/src/deployedAuthorization.test.ts`
- `collabcanvas/functions/src/estimatePipelineOrchestrator.ts`
- `collabcanvas/functions/src/index.ts`
- `collabcanvas/functions/src/integrationRouting.test.ts`
- `collabcanvas/functions/src/openaiAvailability.test.ts`
- `collabcanvas/functions/src/priceComparison.test.ts`
- `collabcanvas/functions/src/priceComparison.ts`
- `collabcanvas/functions/src/serviceIdentity.test.ts`
- `collabcanvas/functions/src/serviceIdentity.ts`
- `collabcanvas/functions/src/storage.rules.test.ts`
- `collabcanvas/src/services/durableStart.test.ts`
- `collabcanvas/src/services/integrationRouting.test.ts`
- `collabcanvas/src/services/pipelineService.ts`
- `docs/setup/durable-execution-v1.md`
- `docs/setup/pre-live-validation-status.md`
- `docs/setup/production-blocker-remediation-v1.md`
- `docs/setup/production-readiness-v1.md`
- `functions/main.py`
- `functions/services/cloud_tasks_dispatcher.py`
- `functions/services/durable_http.py`
- `functions/services/durable_store.py`
- `functions/services/price_comparison_service.py`
- `functions/services/readiness.py`
- `functions/services/service_identity.py`
- `functions/tests/conftest.py`
- `functions/tests/emulator/test_durable_production_path.py`
- `functions/tests/unit/test_durable_http.py`

## 4B — clarification producer / durable start contract

The static producer/consumer gate is now validated offline against the actual application assembly, not just the kitchen JSON fixture. `clarificationAgent` produces conversational extracted data; `EstimatePage.buildClarificationOutput` calls `estimationPipeline`, whose `assembleClarificationOutput` uses annotation quantities, CSI mapping, project-specific extraction and schema checks before storing `projects/{project}/estimations/{session}.clarificationOutput`. The frontend then calls the Python start endpoint (or the authenticated Node forwarder forwards it).

Normal and minimum completed assembly results satisfy every required Python v3 field. The confirmed integration defect was completion-state handling: producer validation warnings could leave output marked complete, the frontend could forward review-required data, and the consumer schema allowed `needs_review`. Producer blocking warnings now preserve original data and mark it for review; stored status follows that marker; EstimatePage refuses to submit it. Durable HTTP start requires complete/no-verification-needed status, nonblank required location fields and strict JSON schema types. No missing construction values are synthesized by this correction.

Cross-language tests invoke the real pure producer/annotation/CSI helpers, then the real Python durable-start handler with fake authentication and in-memory atomic persistence. They cover normal/minimum output, missing CAD, invalid enum/numeric type, review flags, the actual reachable debug fallback builder, optional estimate/project identity contradictions, verified UID versus payload identity, identical duplicates and changed input with a reused key. Source estimate identifiers remain handoff metadata; accepted estimate/job IDs are derived by the durable core from the idempotency key. Optional explicit request estimate IDs must agree with the submitted clarification ID. No agents, providers or real Firestore documents are invoked/created by these tests.

The legacy placeholder debug output is rejected intentionally, not upgraded by invented data. Live model reasoning/output quality is still a separate authorization gate. The former remaining-gate item 4 is satisfied for the current deterministic producer/start contract; it does not authorize a live clarification-provider request.

## Pre-live gate continuation after 4B

See [pre-live-validation-gate-v1.md](pre-live-validation-gate-v1.md) for the current sections 5–18 evidence and unexecuted live plans. 4B remains PASS and was not repeated. All 11 Storage cases now pass against an isolated cached Storage emulator with unchanged rules; the historical timeout's exact cause remains unproven. This supersedes the Storage test blockage recorded above, not the separate bearer-link policy gate.

The continuation found and fixed development-project URL path acceptance in production routing guards. Runtime is still NO-GO: Python liveness is healthy but predates current boundary source, Auth/Functions listeners are unavailable, and current provider/control environment and effective Node disable state are not attested. Local PDF is PASS; managed native packaging remains production-only blocked. No live provider, Timeline-005, Final-live or TEST-008 execution occurred.

## Local runtime readiness refresh V1

The current-session local runtime gate is now PASS; see [local-runtime-readiness-refresh-v1.md](local-runtime-readiness-refresh-v1.md). Python was restarted by the user from its private shell and verified through current process timing plus an authenticated emulator Firestore round-trip. Auth 9099 and Node 20 Functions 5001 are running; callable OPTIONS checks return 204 and the worker's OpenAI disable guard is effective. Firestore 8081 and Python 5003 remain available. Temporary smoke users/documents were cleaned.

This supersedes the earlier runtime NO-GO for the current guarded launch, not production deployment gates. The local-only bootstrap normalizes Firebase emulator K_SERVICE and blocks external networking; ordinary restarts require renewed attestation. Timeline-005 remains unused and is ready only for a separately authorized single execution. No Timeline, Final-live, TEST-008 or provider request was executed.

## Zero-provider final preparation / TEST-008 safety gate

See [zero-provider-final-preparation-v1.md](zero-provider-final-preparation-v1.md).
The fixed-fixture LLM ceilings were independently confirmed as 15 first-pass,
74 synchronous and 222 conservative durable requests. A new opt-in local HTTP
budget caps controlled TEST-008 at 20 attempts across loops/threads/restarts,
with a persistent create-once counter and no OpenAI endpoint allowance.
The running Python process has not been restarted to load that new guard;
TEST-008's prepared runner refuses the old health response. A private-shell
refresh and guard attestation are required before TEST-008, not performed here.

All reserved live IDs remain unused. Timeline-005, Final-live and TEST-008
runners are prepared, not executed. Current local liveness remains healthy;
Gemini quota and separate per-step authorization remain live gates. This task
recorded 711 distinct passing tests (including 51 Firestore, 41 rules and 53
local PDF tests), not cumulative with earlier totals. No provider requests,
cloud changes, commits or pushes occurred. Section 4B remains PASS.

## Timeline-005 consumed — length-limit correction

Timeline-005 was subsequently executed once and failed with
`LLM_INVALID_RESPONSE / invalid_json`, completion status `length`; no Timeline
output was persisted. **Its ID is consumed and must never be reused.** Earlier
unused-ID statements and Timeline-005 execution commands are historical only.
See [timeline-005-length-audit.md](timeline-005-length-audit.md): the Timeline-only
allowance is now 4,096 instead of 1,400 completion tokens, with unchanged prompt,
contract and zero retries. All 166 relevant offline tests passed. No live call
was made for the correction, and the server was not restarted to load it.
Timeline-006 is only a proposed fresh ID, not checked/created/executed. Final-live
and TEST-008 remain unexecuted and separately gated.

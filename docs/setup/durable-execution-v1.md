# Durable execution + service authentication V1

Status: **design resolved; production implementation and cloud validation pending**.
Prepared offline. No cloud resources, credentials, provider requests, or deployment.
The prototype is not connected to `main.py`. Existing synchronous behavior remains.

## Repository evidence and present failure modes

- `functions/main.py:135` declares a 540-second start handler. Despite its immediate/background docstring, `start_deep_pipeline:141` calls `asyncio.run(_start_pipeline_async(...))`; `_start_pipeline_async:259` creates the estimate with `create_only=True` then awaits the complete orchestrator.
- `services/firestore_service.py:583` implements create-only persistence. Authentication/authorization precede creation. Existing IDs conflict rather than returning the previous accepted job. Without a stable client idempotency key, a client retry without an estimate ID generates another ID. Atomic estimate creation prevents overwriting one ID, not duplicate logical requests.
- `agents/orchestrator.py:95` initializes fresh status and in-memory accumulated context on every call, then executes all seven agents. Persisted outputs alone do not implement resume. `_run_agent_with_validation:363` runs primary, scorer, optionally critic and primary retries in the same request. MAX_RETRIES permits two quality retries. A structured primary rate limit permits one coordinated retry only with a safe delay at most 15 seconds; quota/auth stop, and structured scorer/critic failures stop. Preserve these policies.
- `_call_primary_agent:580` creates Cost attempt UUID/expiry and ends it in `finally`. `firestore_service.py:79-132` atomically fences Cost writes. This is not a pipeline-wide lease. Other agent writes, progress, and finalization have no worker generation fence.
- Agent output saves (`firestore_service.py:318`) and pipeline status (`orchestrator.py:726`) persist separately. Final writes authoritative root money in `agents/primary/final_agent.py:287`; orchestration subsequently marks estimate `final` and project pipeline `complete`. These are separate operations. An ordinary failed-stage return persists estimate failure but does not use the exception branch's project-error synchronization. Reconciliation must cover both paths.
- A2A requests have 300-second timeouts and private endpoint declarations. Cost retains a 240-second execution budget and a shared 20-second optional enrichment budget. Node comparePrices declares 540 seconds (`priceComparison.ts:695`). A complete primary/scorer/critic retry cycle is not guaranteed to fit 540 seconds either.

Timeout/termination can leave running progress and partially committed outputs; forced termination need not execute Python exception/finally cleanup. An already accepted downstream HTTP request can continue and write after its caller disappears. Repeating `run_pipeline` resets progress and reruns completed stages. It can duplicate provider charges and writes. Current Cost fencing limits Cost-specific late writes but does not make all seven stages idempotent. Root final output can exist without final status or project completion. No durable processing dispatcher/reconciler exists; a deletion trigger is unrelated.

## Alternatives

These are compatible architectural patterns for the intended Firebase/GCP target, not claims about configured resources. Repository timeout declarations are known; current Cloud Tasks dispatch limits, callable/HTTP generation limits, Pub/Sub/Firestore trigger deadlines, task-name retention and regional quotas require platform verification before deployment. Source comments asserting a platform maximum are not sufficient evidence.

| Model | Durability/auth/retries | Fit, complexity, and Firestore changes |
| --- | --- | --- |
| Cloud Tasks → private HTTP worker | Persistent queue, OIDC service identity, redelivery possible; explicit retry controls | Good fit for current HTTP A2A. Needs job leases, checkpoints, outbox, task reconciliation and invoker IAM. Fake adapter locally. Queue alone does not solve worker duration. |
| Pub/Sub worker | Durable event delivery with platform trigger identity; redelivery possible | Needs equivalent leases/checkpoints and dead-letter/reconciliation policy. Delay/coordinated retries less direct than scheduled tasks. Still must split work. Topic/subscription operations add migration surface. |
| Firestore-trigger worker | Persisted write drives event, platform trigger identity; duplicated/out-of-order events possible | Avoids initial dual-write queue gap but requires strict revision/lease gates to avoid recursive invocation loops; function deadlines still apply. Trigger integration and delayed recovery needed. |
| Stage-by-stage durable dispatch | Delivery guarantees inherited from transport; checkpoint each bounded transition | Selected with Cloud Tasks. A stage must be subdivided into primary/scorer/critic work units, because its complete quality loop can exceed one worker deadline. More state than current code, but required for bounded work and preserving retries. |
| One queued worker runs all seven | Queue durable; execution still bounded by function/request deadline | Smallest superficial code change, but does not resolve the demonstrated duration risk. Requires different verified long-running orchestration platform or checkpointing; rejected for this MVP target. |

## Selected MVP: Cloud Tasks, bounded work units, Firestore checkpoints/outbox

One work unit performs **one A2A operation**: primary OR scorer OR critic. Transition logic preserves the current quality and provider policies. Stage completion means scorer acceptance, not merely a saved primary output. Finalization is a separate idempotent database transition, not another LLM generation.

No in-process background execution. The worker awaits its operation and async transport cleanup before responding. Keep A2A 300 / Cost 240 / enrichment 20 unchanged. Configure and verify worker/task deadlines with margin above the single 300-second A2A bound, and below the verified platform ceiling. Do not deploy if that interval is unavailable. No scorer is appended to a primary work unit. Short provider retry delays become persisted scheduled work, not a fresh quality cycle or stacked SDK retries (SDK remains zero).

### Start

1. Verify Firebase user and authorize project using existing checks.
2. Require a stable client operation key generated once per intentional estimate; transaction maps `(owner, project, key)` to one estimate/job. Store canonical input fingerprint and schema/code version. Same key/different input returns conflict; another owner cannot discover the job. Concurrent same-key starts return the same job.
3. Atomically create estimate, immutable inputs, job cursor and pending dispatch intent in Firestore. The transaction is the durable acceptance boundary.
4. Attempt Cloud Tasks enqueue with deterministic job/revision task identity, exact configured private worker URL and OIDC audience. Return HTTP 202 plus estimate/job ID promptly after enqueue acceptance. On enqueue failure return a sanitized retryable service error; the persisted intent remains recoverable. A same-key retry cannot create another job. Do not return 202 in an unconfigured production dispatcher mode.
5. An outbox event publisher and a scheduled pending/expired-job reconciler close the commit/enqueue gap and recover exhausted/missing deliveries. Both are idempotent. A queue call outside a Firestore transaction is never claimed to be atomic with it. Task-name deduplication is an optimization, not the correctness boundary.

Frontend keeps observing Firestore, accepts 202/queued, retains operation key across lost responses, and does not treat acceptance as completion. Existing Node start forwarding must also accept the new response contract. Production mode must fail startup/closed when real dispatcher/store/config are absent; explicit local synchronous mode may remain separate.

### Worker and persistence

Task contains job ID and expected revision only; fetch trusted project, owner and inputs from job state. Platform IAM authenticates dispatch before parsing work. Firestore transaction claims only the current revision, nonterminal job and expired/absent lease; increments epoch and sets a bounded expiry. Live duplicate delivery must never start a second operation. It may be acknowledged because the independent reconciler is responsible for expired leases; it must not erase the pending intent.

Persist job state: owner/project/operation key, immutable input/version, cursor `(stage, phase, qualityAttempt, providerRetryCount)`, revision, epoch, lease expiry, accepted-output references, candidate output/score/critic references, sanitized error and pending dispatch revision/schedule. Keep bulky outputs in existing subcollections or attempt-scoped documents, not task payloads. Server-only rules protect job/outbox mutations. Reconcile existing frontend progress fields from these transitions.

Every commit checks epoch, lease, revision and nonterminal state **in the same transaction**. This includes agent output, status, Cost items, Final root money and project progress. Propagate a trusted execution token through private A2A calls; update persistence helpers for all agents. A worker-only fence is insufficient because agents currently write directly. Use attempt-scoped candidate output and promote only the accepted attempt. Preserve Cost's existing inner UUID/expiry fence and add the outer job fence; never replace it or enable the direct-call no-attempt branch in durable production execution.

Checkpoint output/cursor and next dispatch intent atomically. On final success atomically promote approved Final monetary fields and mark estimate/job/project completion where transaction limits allow; otherwise an idempotent finalization cursor with fenced writes and a completion marker must be fully reconciled before reporting completed. Do not recalculate money. Readers must not expose unaccepted/stale Final candidates as authoritative.

Resume accepted stages; do not rerun Location/Scope merely because Cost's worker crashed. Rehydrate context from version-matched accepted output references in canonical sequence. Resume scorer after a committed candidate primary result; resume critic/retry after its checkpoint. In-flight work without a checkpoint may be repeated and incur duplicate provider cost: exactly-once external calls are not promised. Validate checkpoint schema/version; incompatible state fails closed for manual review rather than silently restarting.

### Failure table

| Event | Required outcome |
| --- | --- |
| Crash after accepted Scope | Reconciler enqueues the persisted next revision (Compliance); accepted Scope reused. |
| Crash during Cost | Lease expires; new epoch and new Cost attempt claim. Old epoch/Cost attempt cannot commit; isolated comparison IDs prevent result reuse. |
| Quota/auth | Persist specific nonretryable structured error, terminal state, invalidate leases; no scorer/critic/quality retry. Acknowledge task. |
| Provider timeout | Preserve current structured timeout policy (terminal currently); queue infrastructure retries must not reinterpret it as a quality failure. |
| Transient rate limit | Persist existing bounded provider retry count/delay once; dispatch scheduled revision. |
| Worker HTTP timeout/crash | Transport redelivery or reconciler resumes only uncommitted work after lease expiry; bound infrastructure attempts and persist exhaustion terminally. |
| Duplicate start | Same authorized key/input returns existing job; mismatch conflicts. |
| Duplicate/out-of-order task | No work unless revision/lease matches; completed delivery acknowledged without mutation. |
| Stale worker resumes | Transaction rejects writes even if its downstream A2A returns HTTP 200. |
| Final commit succeeds, ACK lost | Redelivery sees terminal state and acknowledges; no Final LLM call or monetary recomputation. |

## Python → Node pricing service identity

Current `services/price_comparison_service.py:77` sends no Authorization. `get_material_prices:252` intentionally appends `--pricing-{uuid}` to its input project identifier, which `_get_material_prices:277` places in callable `request.projectId`. Node's shared authenticated callable wrapper now checks real project ownership and `req.auth.uid`. It rejects this unauthenticated request; existing optional fallback is correct. Simply adding an ADC token to a Firebase callable is not a valid solution: Firebase end-user authentication and service OIDC are different boundaries.

Keep Node pricing to reuse existing comparison/matching integrations. Moving them to Python would introduce unnecessary behavior/dependency migration. Extract existing comparison processing into a shared internal function; retain browser `comparePrices` callable with its Firebase user/project authorization. Add a separate private Node HTTP `comparePricesService` entrypoint for Python ADC invocation, no browser CORS or anonymous production invoker.

Caller: dedicated Python agent runtime service account (Cost executes in its A2A runtime). Obtain an OIDC ID token via ADC for the exact deployed service endpoint audience; send Bearer Authorization. Platform IAM validates signature/issuer/audience and invoker principal before Node runs. Do not trust user-supplied principal headers. Confirm the actual deployed endpoint URL/audience and Functions generation IAM mapping in staging; use narrowly scoped invoker grants, not allUsers. Platform protection is mandatory; optional application verification must validate issuer/audience/allowed caller identity, not merely decode JWTs.

Separate `projectId`, `estimateId`, `comparisonId`, and active execution/Cost attempt token. Load trusted job linkage and authorize the real project association; do not interpret synthetic comparison ID as a project. Store results under an isolated comparison namespace, with expiry/fence semantics. Keep optional 20-second budget/fallback and no per-item retry storm. Node may outlive Python cancellation; late comparison work must not mutate authoritative Cost state. No service credentials in browser or Firestore task data.

Local-only: emulator URLs remain Node 5001 and Python A2A 5003. Explicit emulator configuration may use a localhost-only trusted service test adapter without ADC, with production guards rejecting it. No production fallback on local failure. Preserve Node DISABLE_OPENAI and sentinel handling; do not change secret values. Existing callable cannot be used anonymously as a shortcut.

A2A already uses `services/a2a_client.py` ADC `fetch_id_token` with target endpoint audience and private declarations in `main.py`. Apply this same identity pattern to queued workers and pricing. Distinct least-privilege accounts may share a helper, not end-user tokens. Actual IAM is not verified by offline tests.

## Offline prototype and its limits

`functions/prototypes/durable_execution.py` defines start/claim/finish/dispatch boundaries with an unconfigured dispatcher that raises. It is deliberately NOT imported by deployed endpoints. Memory store and queue exist only in `test_durable_execution.py`. Start never runs agents; tests deliver work explicitly. The fake store models atomic claim/lease/epoch/revision and next-intent checkpointing, including dispatch gaps, stale attempts, cancellation, terminal failure and lost acknowledgements. No threads, network, SDK, Firestore or provider calls.

The seven-unit prototype treats a mocked accepted stage as one unit to prove sequencing; production must use primary/scorer/critic cursors described above. It does not implement real queue/store adapters, reconciler, endpoint auth, all-agent write fencing, retry-state extraction, or production finalization. Its fixed failure code tests sanitization only; production must retain StructuredError categories and policies. The existing mocked seven-stage integration test independently exercises today's orchestrator/A2A/persistence and approved Final contract. Neither test is proof of cloud durability.

## Implementation / authorization / rollout

### Code changes (next implementation scope)

1. Implement transactional Firestore job/outbox adapter, immutable input/idempotency mapping and reconciliation tests; implement Cloud Tasks adapter behind fail-closed configuration.
2. Extract existing quality transitions into bounded work-unit cursor operations; preserve counters, fallback policies, scores and all mathematics. Add primary/scorer/critic checkpoint and cancellation tests.
3. Propagate job fence into all A2A and persistence writes including Final/project completion; retain Cost inner fence. Test late remote completion after lease takeover, partial finalization and crash recovery.
4. Add private worker and outbox/reconciler handlers; change authenticated start to accepted response only when real adapters configured. Update frontend and Node forwarding for accepted/idempotent semantics.
5. Add private Node pricing service wrapper/shared core plus ADC client, trusted linkage validation and isolated results. Test no-auth/wrong-audience/wrong-principal/wrong-project locally with fake identity verifiers.

### Cloud resource creation — explicit human authorization required

Enable/verify required Cloud Tasks/Functions/Firestore identity and scheduling APIs, provision regional queue and reconciliation trigger/schedule, dead-letter/operational exhaustion handling, runtime and dispatch identities. Verify supported deadlines, regional co-location, naming retention, retry caps and transaction/document limits before provisioning. No values are assumed from outdated source comments.

### IAM — explicit human authorization required

Start/publisher identity: only needed Firestore access and queue enqueue permission. Task dispatch identity: invoke only private worker. Cloud Tasks service agent: required token minting for that dispatch identity; creator's act-as binding where required, verified against deployed setup. Worker/agent runtime: necessary Firestore access and invoke only required A2A/pricing targets. Pricing runtime: only existing pricing data/provider permissions. Reconciler: job/outbox access and enqueue. Keep user-facing start Firebase authentication; never grant end users worker invocation. Verify exact `roles/run.invoker` versus generation-specific Functions invoker grants on deployed resources; do not grant both indiscriminately.

### Secret/environment configuration — human-controlled cloud change

Explicit project/region, queue, worker URL/audience, identity names, dispatcher mode and retry/lease bounds. Existing Gemini/OpenAI provider selection and secrets unchanged. No static service secret or key file. Production rejects emulator settings. ADC uses workload identity. Local test config is separate.

### Deployment and post-deploy validation — separate authorization

Deploy private handlers and IAM first, validate authentication denies anonymous/wrong identity without invoking an agent, then enable dispatcher/start mode. Test with fake seven-stage executor in staging before any paid pipeline. Exercise enqueue outage, duplicate IDs/tasks, lease expiry, each checkpoint crash, late A2A writes and lost final ACK. Verify root money/project progress and no duplicate accepted outputs. Real provider validation requires its own authorization and quota budget.

Rollback: stop accepting new durable jobs, pause dispatch deliberately, retain job/outbox evidence. Do not route queued jobs into synchronous `run_pipeline`. Drain compatible revisions or fence/cancel them before rollback; retain schema-versioned worker for old jobs where safe. Roll back mode only for new requests under explicit control. Never delete accepted jobs to make rollback appear complete.

## Decision

540-second blocker: DESIGN RESOLVED, implementation pending. Pricing auth: DESIGN RESOLVED, implementation pending. Ready for scoped application implementation; cloud setup/deployment requires human authorization. Platform verification remains a deployment gate. No existing production endpoint behavior has changed in this task.

## Durable Execution Core V1 — implemented offline

This section supersedes the earlier prototype-only status for the **persistence/state-machine core**, not for cloud delivery or deployed endpoints. The original `prototypes/durable_execution.py` remains a design example. New application modules are `services/durable_execution.py` and `services/durable_store.py`. Neither is imported by `main.py`; the local synchronous path is untouched.

### Work-unit decision

A primary and scorer can each consume their existing 300-second A2A budget. Adding critic makes the bound worse. Therefore combining them cannot guarantee a bounded worker without altering timeouts or quality behavior. Passing stages require **two** deliveries, primary and scorer; critic is conditional, not a mandatory third delivery. A seven-stage passing run uses 14 mocked operations. No extra finalization task: accepting Final's scorer atomically commits job, accepted output, authoritative monetary mapping, estimate final status and project complete status.

The core defaults to a 330-second cooperative async operation bound, 360-second lease, and three infrastructure claims per operation. These are NEW durable-core bounds, not modifications to current A2A/Cost limits. Cost's inner default remains 240 seconds. No threads are used. Cancellation remains cancellation and leaves a recoverable lease. Existing stage quality retries default to two; future adapters must pass the existing configured quality policy. Infrastructure recovery does not increment quality retries. Platform dispatch/deadline validation is still required.

### Actual persistence model

- `durableJobs/{jobId}` contains schemaVersion, jobId, estimateId, projectId, ownerUid, inputFingerprint, status (`queued`, `running`, `failed`, `final`), currentStage, currentOperation, generation, revision, attempt, qualityAttempt, providerRetries, leaseOwner, leaseExpiresAt, createdAt, updatedAt, lastError, progress and accepted checkpoint references.
- `generation` increments on claim/takeover; `revision` increments on a committed transition. `attempt` counts infrastructure claims of one revision. Accepted stage references are distinct from unscored `candidateRef`; `criticRef` preserves feedback.
- The **embedded transactional outbox** is `dispatchIntent`: operationId, revision, status (`pending`, `dispatched`, terminal `completed`/`cancelled`), notBefore, publication. No separate outbox document is needed for one pending operation per job. A completed operation has an immutable checkpoint; the next intent replaces the current intent in the same transaction. Query/index adapters for scanning pending/expired work remain to be implemented.
- Immutable outputs are `durableJobs/{jobId}/checkpoints/{operationId}`. Only scorer acceptance promotes a candidate to canonical `agentOutputs`/root output. Business outputs are stored intentionally; failure metadata is reconstituted through `StructuredError`, never arbitrary exception messages.
- Start takes an adapter-created `AuthorizedStart`, a stable operation key, and clarification. SHA-256 of that key yields deterministic job/estimate IDs. Owner/project/input fingerprint must match on reuse; a conflicting key is rejected rather than revealing/reassigning the existing job. Inputs live on the estimate, not duplicated in the job. Key issuance/retention and Firebase user/project verification remain at the HTTP adapter boundary.
- Core `start()` records durable intent and returns an accepted **representation**, without executing or publishing work. Production construction fails closed without an explicitly production-ready dispatcher. This is not a deployed 202 endpoint: queue integration must define/enforce its enqueue/acceptance policy.

### Atomicity and Firestore boundary

`JobRepository` performs up to three optimistic CAS attempts. Its callback commits the job version together with all checkpoint, estimate, and project writes. `FirestoreAtomicBackend` accepts an injected **AsyncClient**, uses an update-time precondition or create-only batch, disables SDK retries, and bounds read/commit RPCs to three seconds. It does not create a client, obtain credentials, or contact Firestore during offline tests. Duplicate writes to one document are combined before the atomic batch. Real Firestore emulator/cloud execution of this adapter is still unverified; tests exercise its batch construction and a concurrent CAS fake.

A storage failure before commit leaves no partial checkpoint or intent. Failure after server commit/before acknowledgement is handled by rereading state: old revision cannot claim or complete. No transaction callback contains provider or dispatcher calls. An enqueue succeeds before `dispatched` is recorded; a lost acknowledgement can republish the same deterministic task name harmlessly. Recovery increments the publication suffix (`operationId-pN`) to avoid relying on recreation of a task name retained by Cloud Tasks after completion. Operation identity itself stays unchanged, so duplicate old/new deliveries share the same lease gate. No task-name retention limit is assumed.

`recover()` reopens expired work or marks bounded delivery exhaustion failed. It does not run work or scan the database. A real scheduled reconciler and publication adapter are still required. A worker crash before checkpoint may cause the external operation to run again; accepted output/finalization is fenced, but exactly-once external provider requests are not promised.

### Failure and quality transitions

Quota/auth/invalid response/insufficient input/provider timeout/provider error remain structured terminal failures. A primary `LLM_RATE_LIMITED` with a safe delay at most 15 seconds receives the existing single coordinated retry, persisted in `providerRetries` and `notBefore`. Scorer/critic structured errors stop; no quality loop is entered. Unstructured A2A primary errors consume the existing quality retry budget; unstructured scorer failure retains the existing score-80 pass fallback, and critic failure retains basic feedback. Successful low-quality outputs still go scorer → critic → primary until the configured quality limit.

Generic unexpected errors persist only `PIPELINE_FAILED`; raw messages/bodies are discarded. Cancellation does not become provider failure. A worker operation deadline uses the A2A timeout policy, not a fabricated provider-quota diagnosis. SDK/provider retry settings are unchanged.

### Cost compatibility and remaining write boundary

A durable Cost primary claim atomically establishes its generation-derived inner attempt in both job and root `costAttempt`, using the existing id/active/expiresAt shape. Completion requires both the outer lease/revision/generation fence and active unexpired root Cost attempt; the root update-time precondition catches intervening revocation. Primary checkpoint closes the inner attempt atomically. Retry/terminal paths revoke it. Tests invoke the unchanged `FirestoreService._write_cost_attempt` and `end_cost_attempt` callbacks against the shared fake root state to verify the existing guard accepts the current attempt and rejects old/revoked attempts.

**Not yet integrated:** real A2A agents still perform direct persistence through existing helpers. Before connecting this core to them, propagate the outer token through every write and stage candidate output (including Final root writes), so no downstream process can bypass the durable commit fence. The current core accepts mocked/adapter outputs only. Its Final adapter contract expects authoritative monetary fields already mapped at top level; it copies them without recalculation and does not invent missing percentiles. Actual Final output mapping must reuse the approved existing mapper. Project-active-job authorization/progress integration also needs adapter validation when multiple estimates belong to one project.

### Worker and dispatcher contracts

The worker body accepts exactly `jobId`, `revision`, `operationId`. Identity, owner, stage and outputs cannot be supplied in that body. `Worker` requires an adapter-issued `VerifiedService` whose principal and audience match configured values before claiming. Tests inject fake verified identity; no JWT bypass is installed in an HTTP endpoint. Production verification remains IAM/OIDC at the private HTTP boundary, not construction of a dataclass from request headers.

Dispatcher protocol accepts deterministic task ID, identifier-only payload and notBefore. Fake dispatcher exists only in tests. Missing production dispatcher rejects configuration. No Cloud Tasks package, queue, credentials, IAM or background threads were added.

### Validation and next gate

Deterministic tests cover start conflicts, concurrent claims, failed atomic commits, publication loss, lease expiry, stale writes, structured failure policy, quality transitions, Cost revocation, finalization loss and mocked 14-operation/seven-stage completion. The approved fixture is copied exactly: finalEstimate/totalCost 31,456.74; P50 29,283.60; P80 32,797.63; P90 35,067.77. Existing orchestrator, authenticated request, Cost fencing, Final monetary and integration suites remain required regressions.

Next: implement an isolated Cloud Tasks adapter and pending/expired intent scanner, validate the injected Firestore adapter against emulators under separate local-test scope, then implement live A2A fencing/Final mapping/auth adapters. Do not connect production or authorize live AI yet. No cloud durability claim is made from fake-store tests.

Core V1 validation result: **163 passed** with an autouse socket-blocking fixture: 46 new core tests, 9 earlier prototype tests, 3 integration wiring tests (including the existing mocked seven-stage E2E), 15 orchestrator tests, 19 Cost safety tests, 26 request authorization tests, 23 Final offline monetary tests and 22 production-configuration tests. Changed Python files compile; tracked and new-file whitespace checks pass. No provider/Firestore/cloud request, live pipeline, deployment or service restart occurred.

## Durable Execution Emulator Integration V1

**Real local Firestore persistence validated; real agents and Cloud Tasks remain disconnected.** The existing `start_deep_pipeline` and synchronous development behavior are not replaced.

### Persistence and atomicity evidence

The injected `FirestoreAtomicBackend` now ran against Firestore on **127.0.0.1:8081**, in random, isolated `demo-durable-*` projects. It uses actual server-atomic Commit batches with create-only and update-time preconditions, **not** in-memory transaction emulation and **not** the BeginTransaction API. Tests deliberately force simultaneous reads of a missing job and conflicting server commits; exactly one creation succeeds and the other returns that job after retry. Deliberate checkpoint/create conflicts roll back the entire batch, including job cursor, next intent, and Final root/project fields.

Output/checkpoint and required next dispatch intent remain in the same atomic commit. Lost acknowledgements are tested by redelivering an already completed operation. Fenced output cannot be overwritten. A passing Final scorer commits root authoritative values, final job status, progress 100 and project completion together, with no next task. No money is recalculated. Firestore's map ordering is not used for sequence: project `completedStages` now explicitly follows `AGENT_SEQUENCE`.

### Bounded scanner/reconciler

`FirestoreAtomicBackend.scan_jobs()` returns a stable document-ID page, limited to 1–100 jobs; no composite index is required. `IntentScanner.run_page()` defaults to 25 jobs and a 20-second cooperative timeout. It returns only visited/published/error counts, continuation cursor and timeout state. The caller must retain the cursor across invocations and start a new pass after its end; this prevents terminal documents at the beginning of the collection starving later jobs. A production scheduler/scanner invocation adapter is still required; there is no daemon or background thread.

No publication lease is necessary: duplicate scanner publication uses the same deterministic task name, and the dispatcher must treat already-existing tasks as accepted. The fake dispatcher records duplicates but stores one task per name. A publisher dying after enqueue and before marking leaves a recoverable pending intent. The scanner skips live worker leases and future retry schedules. Newly dispatched tasks receive a delivery grace window equal to the lease duration; they are not immediately considered abandoned. An expired worker is put back into queued state before republication, so simultaneous recovery scanners share one publication generation. Lost-delivery republication is bounded; persistent exhaustion terminates safely. Publication-generation suffixes remain distinct from operation/revision identity.

### Isolated start and output boundaries

`services/durable_boundaries.py` adds:

- `DurableStartService`: invokes existing Firebase token authentication, reads the real project asynchronously and reuses `request_auth.authorize` for owner/editor and claimed-user checks. The shared helper gained an optional document-reader injection; default behavior is unchanged. Durable estimate identity comes from the stable key, not a client-supplied historical fixture estimate ID. It returns `{httpStatus: 202, estimateId, jobId, status: "accepted"}` after persisting intent and never runs a worker. Firebase signature verification was mocked in local tests; project data and role authorization used the real emulator. A deployed HTTP wrapper, including its existing origin/response policy, remains deferred.
- `DurableWriteToken`: job ID, revision, operation ID, generation, claim attempt, lease owner and Cost attempt identity. `Lease` now carries its claim attempt, and the outer fence checks it.
- `DurableOutputBoundary`: requires verified service principal/audience, resolves target paths from server-side job state and accepts only the current lease token. Primary output is a candidate; scorer acceptance promotes it. A missing Cost attempt cannot bypass the Cost guard. Revoked/stale Cost identities remain rejected.

**Outer A2A wiring remains partial.** Audit of current direct persistence finds `BaseA2AAgent` status writes; Location/Scope/Compliance/Cost/Risk/Timeline/Final output saves; Cost item saves; Final root integration writes; and orchestrator status/output/project writes through `FirestoreService`. None has been routed to real durable execution. Before real agents connect, replace/stage these direct writes behind the outer token contract and protect progress/final projections as well. Do not treat the new boundary as protection for legacy callers that do not yet use it.

### Rules and local safety

An explicit recursive deny protects `durableJobs` and every child path, including current embedded intents/leases and immutable checkpoints. The prior catch-all already denied these paths; the explicit rule documents the server-only boundary. Emulator tests prove owner/editor/viewer/unrelated/anonymous clients cannot forge generation, lease, cursor, intent or checkpoint writes. Admin/test-privileged writes work; estimate owner reads remain available. Existing project pipeline display rules are unchanged and are not trusted as durable execution authority.

Python tests require the exact emulator host before client creation, inject anonymous emulator credentials, prohibit all other gRPC channels/Python socket connections and disable gRPC proxies. Node rules tests use the existing nonlocal-network blocker. There are no provider requests or Functions invocations. Each new Python test owns an isolated demo project and recursively cleans only its documents; rules tests clear their isolated demo fixtures and close contexts. The existing security rules suite now also clears its own demo fixtures on teardown. Application project data and services are untouched.

### Validation result and remaining gate

25 real-Firestore integration tests passed, including mocked seven-stage execution with a Scope critic/quality retry (17 operations), canonical progress order and the approved monetary fixture. 175 socket-blocked Python regression tests passed, including 12 new service-boundary tests. 30 Firestore rules tests passed (21 new durable rules tests plus 9 existing security tests). **230 relevant tests total.** Real queue delivery, IAM/OIDC verification, production RPC/deadline behavior and every real-agent persistence adapter still require separate implementation/validation. The next scope is the isolated Cloud Tasks adapter and scanner invocation contract, followed by full real-A2A write fencing before live AI authorization.

## Durable Agent Wiring V1 — current local implementation

This section supersedes the earlier **not yet integrated / partial outer A2A wiring** statements for the injected local durable worker path. The public start endpoint and ordinary synchronous development route remain unchanged. Production HTTP/IAM and real queue delivery remain unimplemented.

### Actual write audit and binding

`DurableAgentExecutor` runs the existing primary/scorer/critic A2A handlers **in process**, inside the worker's event loop. Its server-owned factory injects `FirestoreService` with an immutable `AgentWriteContext`. It reloads the authoritative job and accepted checkpoint inputs; model output cannot select a job, token, stage, destination or owner. The existing LLM async context closes before the worker loop ends. This is not a remote HTTP A2A authentication bypass or a second public endpoint.

The audited write paths are:

| Existing path | Durable treatment |
| --- | --- |
| BaseA2AAgent `update_agent_status` | Fenced attempt-local status |
| Location, Scope, Compliance, Risk and Timeline `save_agent_output` | Fenced candidate staging |
| Cost output and per-division `save_cost_items` | Outer fence plus existing active/unexpired Cost attempt; accumulated staged ledger |
| Final `update_estimate` integration mapping and output save | Staged approved root mapping; scorer acceptance commits monetary fields atomically |
| Scorer/critic A2A return values | Executor persists through the same bound output service; scoreRef ties critic feedback to its scorer revision |
| Legacy orchestrator/root/status/output/project sync and deletion | Refuse a root with `durableJobId` when no durable context is supplied |

These were authoritative writes outside the outer fence before this change. Existing application mutators now reject unbound durable writes; ordinary non-durable estimates retain their prior behavior with one bounded root-ownership read. Direct out-of-band Admin SDK writes are not an application security boundary. Durable-aware cancellation/deletion remains follow-up work; the legacy methods fail closed.

Staging is keyed by operation and generation and committed with the current job version. Duplicate identical output is harmless; conflicting duplicates are rejected. Only core checkpoint/scorer/finalization transactions promote authoritative values. Cost additionally checks the root attempt identity, active flag and expiry with a root version precondition. An old outer worker cannot use a still-valid old inner token, and a current outer worker cannot omit or replace its Cost token.

Cost ledger staging accumulates per-division batches and preserves original item IDs. Promotion reads all staged rows in one bounded Firestore RPC, then commits them with accepted Cost output. It caps the atomic ledger at 400 rows and fails closed above that limit; cloud request/document size limits still need production validation. No pricing, monetary, provider, A2A timeout or Cost timeout policy changed.

### Dispatch, worker and scanner contracts

The future Cloud Tasks builder requires explicit `DURABLE_QUEUE_PATH`, `DURABLE_WORKER_URL` (HTTPS) and `DURABLE_DISPATCH_SERVICE_ACCOUNT`. Missing configuration fails closed. The exact wire body is `jobId`, `revision`, `operationId`, `generation`; no outputs, credentials, user tokens or authorization decisions are included. Task names are deterministic `operationId-pN`; OIDC audience is the configured worker URL. Generation rejects obsolete recovered deliveries. The earlier three-field internal envelope remains compatible only inside the core, not at the new wire boundary.

The builder proposes a 390-second dispatch deadline around the existing bounded operation/lease model. No Cloud Tasks SDK call or production-ready dispatcher is supplied. Deployment must validate platform deadlines, queue retry policy and task-name retention behavior before provisioning.

`DurableWorkerService` requires an adapter-verified service principal/audience, reloads authoritative state, and rejects unknown/terminal jobs and obsolete operations/revisions/generations before execution. A future private HTTP wrapper must obtain `VerifiedService` from actual IAM/OIDC verification, never from user-controlled headers or end-user authentication.

`ScannerInvocation` likewise requires a configured verified service identity and accepts only a bounded page limit and optional cursor. Scheduling and cursor retention are external; repeated pages/publications remain safe. Future IAM must restrict worker invocation to the dispatch identity, scanner invocation to its scheduler identity, and queue enqueue permissions to the dispatcher. No queue, scheduler, service account or IAM changes were made.

### Local evidence and limits

47 new tests cover the contracts and real-agent persistence bindings: 23 offline dispatcher/invocation tests and 24 Firestore emulator tests. The emulator tests use actual agent classes, A2A handlers and bound FirestoreService methods, with injected agent/provider results. They do not execute live LLMs. The 17-operation E2E includes a Scope scorer/critic quality retry and executes Final's existing run/mapping with a fake LLM; the other primary computations are fixture-injected. All seven accepted outputs, progress 100, ledger promotion, cleanup and stale Final rejection are checked.

The approved monetary fixture remains base 27,942.71 + contingency 3,514.03 = finalEstimate/totalCost 31,456.74, with P50 29,283.60, P80 32,797.63 and P90 35,067.77 separately preserved. Existing Cost, synchronous integration, auth, Final monetary, LLM lifecycle/provider and rules regressions remain required. Local durable execution is ready for review; this does not remove the legacy public pipeline's production duration limit. Real dispatch, private HTTP/IAM verification and production operational validation remain gates before cloud provisioning or live AI validation.

## Autonomous zero-provider completion — current wiring

The previous deferred **code adapter / HTTP wrapper** statements are superseded by `durable_http.py`, `cloud_tasks_dispatcher.py` and the private functions exported from `main.py`. Real cloud delivery and IAM remain deferred. Production public start selects durable execution or fails closed; local synchronous behavior is retained. The scanner now has an authenticated HTTP composition and real REST dispatcher code, tested with fake HTTP sessions only. Worker composition owns Firestore and LLM cleanup in one invocation loop and uses the real agent factory. Estimate progress is an atomic job-derived projection for existing frontend subscriptions.

See [pre-live validation status](pre-live-validation-status.md) for configuration names, operational recovery, production limits and deployment gates. The new production-shaped emulator E2E exercises authenticated start, 202, outbox, scanner, mocked REST task payloads, verified private worker boundary, all seven agent persistence boundaries, a Scope critic/retry, and Final's approved monetary mapping. A separate test proves HTTP enqueue failure retains pending intent and later task-exists acknowledgment resolves safely. No actual Cloud Tasks, service tokens or LLMs were used.

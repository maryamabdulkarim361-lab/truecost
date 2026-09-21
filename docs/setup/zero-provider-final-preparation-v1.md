# Zero-provider final preparation and TEST-008 safety gate

This is preparation, not live execution or deployment approval. No reserved live
estimate was created, no provider request was sent, and no cloud resource was
changed. The completed 4B contract result remains PASS.

## Current runtime and reserved identities

Local read-only checks in this task found listeners on Python 5003, Functions
5001, Firestore 8081 and Auth 9099; `/health` returned 200. The existing Python
PID 3708 and Node CLI/worker PIDs 6564/8045 remain running. The worker's prior
safe attestation reports Node 20.20.2, OpenAI disabled and local emulator marker
normalization active. No process environment or credential was recovered.
The user's private-shell Gemini/model/emulator attestation from the runtime
refresh remains the evidence for those settings, not a new quota/auth probe.

Read-only Firestore lookups returned 404 for all three reserved IDs:

- `est-local-timeline-005`
- `est-local-final-001`
- `est-local-test-008`

The running Python process predates the **new opt-in budget hook**. Existing
Timeline/Final business behavior was not changed. TEST-008 cannot use this old
process: its prepared runner requires the new `/health` budget attestation.
No services were restarted. Only a task-owned isolated Storage test emulator
was started and stopped; the user's emulator suite was left running.

## Independently checked request ledger

These are conservative **LLM-generation ceilings for the fixed 50-item kitchen
fixture**, not guaranteed usage or counts of retailer/search/BLS HTTP calls.
Optional extraction calls can be skipped when search data is absent.

| Stage | Current call sites in `functions/agents/primary` | Per primary attempt |
| --- | --- | ---: |
| Location | `location_agent.py::_extract_data_from_search`, `_analyze_with_llm` | 2 |
| Scope | `scope_agent.py::_generate_searchable_names` (20-item batches), `_generate_labor_estimates_with_llm`, `_analyze_with_llm` | ceil(50/20)+1+1 = 5 |
| Code Compliance | `code_compliance_agent.py::run`, `_search_permit_fees` extraction | 2 |
| Cost | `cost_agent.py::_infer_project_complexity`, `_analyze_with_llm` | 2 |
| Risk | `risk_agent.py::_search_market_risks`, `_get_llm_analysis` | 2 |
| Timeline | `timeline_agent.py::_generate_task_specs_with_llm` | 1 |
| Final | `final_agent.py::_get_llm_analysis` recommendations | 1 |
| All scorers | Existing deterministic scoring; no generation sites | 0 |
| Each critic | `critics/base_critic.py::critique` generation | 1 |

First-pass ceiling: **15**. JSON generation calls `generate_with_system_prompt`
once, then parses locally; there is no repair generation. All current primary
and critic generation sites use the same async LLMService transport. There are
no nested primary-agent invocations hidden behind these generation sites.

`PipelineOrchestrator._run_agent_with_validation` permits three quality attempts
and one additional coordinated transient-primary retry **per stage**, not per
quality attempt. At most two critic calls per stage: **4×15 + 2×7 = 74**.
Quota/auth structured failures terminate without critic/quality amplification;
a rate-limit retry requires a safe delay at most 15 seconds. SDK retries are 0.
Some earlier optional helpers catch errors and fall back, so not every provider
failure immediately terminates the whole pipeline; later generation sites may
still be reached. This is why relying only on structured error policy is not a
complete spending ceiling.

`DurableCore` defaults to three deliveries per operation. Its provider retry
counter resets only on stage advancement. A conservative crash/redelivery bound
is **3×74 = 222**, not an expectation that ordinary duplicate delivery makes
222 calls. Lease/fence checks reject duplicates, but cannot undo provider work
already performed before a worker crash. TEST-008 will not use durable delivery.

A2A remains 300 seconds; Cost remains 240 seconds with one shared 20-second
optional enrichment budget. Caller timeout does not prove remote execution
stopped. Overlapping attempts can otherwise spend extra requests even when
stale writes are fenced. No timeout, prompt, mathematics, quality policy or
production provider selection was changed here.

Other dependency origins are separate from that LLM ledger:

- Location's SerpAPI wrapper issues up to six search queries (cost of living,
  three permits, weather and union), and one BLS series batch; wrappers allow up
  to three HTTP attempts per query/batch. Cache/fallback may reduce these counts.
- Compliance may issue three permit queries; Risk up to four market queries,
  with the same search-wrapper retry bound. These are not extra Gemini calls
  beyond the extraction sites counted above.
- Cost makes optional batch pricing through local Node 5001, with the shared
  20-second deadline and no missing-item remote-comparison storm after failure.
  Node can search two retailers for each uncached product; its four OpenAI paths
  are disabled. The current local Node bootstrap additionally blocks external
  sockets. A future launch must preserve or explicitly re-review that restriction.
- Firestore reads/writes and A2A are local; synchronous TEST-008 does not enqueue
  Cloud Tasks. A pricing timeout does not prove already-started Node work stopped.

The new guard limits **Gemini HTTP attempts**, not these independent services.
A later TEST-008 approval must explicitly cover any permitted Python search/BLS
calls or retain an external-network restriction. No such calls were made here.

## Controlled TEST-008 ceiling: 20 physical async LLM HTTP attempts

The opt-in `TRUECOST_LOCAL_PROVIDER_BUDGET=test-008` installs a request hook on
LLMService's explicitly owned async HTTP client. It is shared by all current
pipeline primary/critic calls, including direct async model use. It runs before
every outgoing HTTP attempt, including redirects, and permits only the Gemini
OpenAI-compatible HTTPS endpoint. It does not inspect headers or credentials.

The fixed private counter `/private/tmp/truecost-test-008-provider-budget.count`
is created **once by the future runner**, mode 0600, using O_EXCL. A filesystem
lock serializes updates across threads/processes; each slot is fsynced before
sending. Transport failure, cancellation, a new event loop, a new service or a
process restart never refunds/resets a slot. Slots 1–20 may send; attempt 21
cannot send. Missing, corrupt, exhausted, symlinked or non-private state fails
closed. No counter was created during preparation.

Denial is a non-retryable `LLM_PROVIDER_ERROR`, safe reason
`validation_budget_blocked`, stage `provider_request`. This reason survives SDK
connection-error wrapping. Existing optional fallbacks may still run, but every
later outgoing LLM request is denied too. Completion after exhausted budget is
not proof of successful reasoning: review the consumed counter and agent logs.
The counter is a conservative **attempt** count, not an assertion all slots
reached Gemini. It is not a token/dollar budget or a retailer/search quota.

The flag is rejected for production/managed execution, durable mode, non-Gemini,
or nonlocal Firestore settings. An unset flag preserves original transport
construction. Existing synchronous model construction/lifecycle remains intact;
the guarded controlled pipeline uses async generation exclusively. Arbitrary
new synchronous SDK callers are outside this local validation facility.
The operator must not reset/delete/replace the counter or run other live agents
concurrently. The guard is an operator-controlled safety tool, not protection
against a malicious same-user filesystem actor.

## Prepared harnesses — no main function executed

All three scripts are local-only under `/private/tmp`, with explicit
`--execute-once`, exact emulator host, fixed URLs, no proxies, no redirect
following, no client retries and one agent/start POST site outside any loop.
Static AST checks and fixture assembly passed. No runner submitted anything.

1. `/private/tmp/truecost_timeline_005_runner.py`: exactly one Timeline call,
   at most one generation, no orchestrator/scorer/critic. Uses the established
   risk/timeline clarification and matching Cost-family Location/Scope/Cost
   fixtures. Historical values unchanged. Existing-ID read and atomic parent
   create guard submission. Preserves evidence, safe JSON-boundary diagnostics.
2. `/private/tmp/truecost_final_001_runner.py`: exactly one Final call, at most
   one recommendations generation. Uses the existing `test_final_offline.inputs`
   assembly, fixed 2025-01-18 fixture clock and its labelled offline compliance
   stub. No upstream agents run. Same create-only evidence guard. Final may use
   its existing recommendation fallback: completion alone does not prove a
   successful Gemini generation. Money must remain independently reconciled.
3. `/private/tmp/truecost_test_008_runner.py`: exactly one authenticated
   synchronous start, full kitchen JSON with only estimate ID changed. Creates
   a local anonymous Auth identity in memory and an atomic, dedicated
   `local-test-project-008` owned by that verified UID. Does not trust a fabricated
   payload UID. The start handler owns estimate creation. A persistent exclusive
   counter marker blocks concurrent/manual runner reuse, including after an
   interrupted request. A pre-existing project/estimate also aborts. It retains
   test evidence and never logs the emulator token. A timeout is inconclusive;
   monitor the original estimate, never resubmit.

### Future sequence and commands (NOT authorized by this document)

Fresh local readiness and separate explicit approval are required for **each**
step. Do not run these as a batch. No quota probe.

First, after quota availability is established separately and Timeline approval:

```bash
cd ~/Desktop/truecost/functions
FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 venv/bin/python -B /private/tmp/truecost_timeline_005_runner.py --execute-once
```

Review Timeline evidence. Only after separate Final approval:

```bash
cd ~/Desktop/truecost/functions
FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 venv/bin/python -B /private/tmp/truecost_final_001_runner.py --execute-once
```

Review Final reasoning provenance and authoritative money. Before TEST-008,
refresh **only Python** from its original private shell with the already-attested
Gemini/emulator/A2A settings, synchronous mode, and this additional control:

```bash
export TRUECOST_LOCAL_PROVIDER_BUDGET=test-008
# In the original private shell, after stopping only the prior Python server:
cd ~/Desktop/truecost/functions
source venv/bin/activate
python serve_local.py
```

Never recover/retype a key into this document. Re-attest Node OpenAI disable,
local pricing 5001, A2A 5003, emulator routing and current-source lifecycle.
`GET /health` must report `local_provider_budget.enabled=true` and
`maximum_requests=20`; the future runner checks those fields before any creation.
Do not precreate or reset the budget file. Only after separate TEST-008 approval:

```bash
cd ~/Desktop/truecost/functions
FIRESTORE_EMULATOR_HOST=127.0.0.1:8081 venv/bin/python -B /private/tmp/truecost_test_008_runner.py --execute-once
```

Retain the counter and documents on any outcome. If setup fails after marker
creation, stop for review; there is deliberately no automatic recovery that
could accidentally allow a second pipeline. Do not send durable start requests:
its real dispatcher needs a separately reviewed cloud/local dispatch plan.

## Working-tree and release review

The task began with 216 status entries covering prior provider/lifecycle,
auth/security, durable execution, monetary/PDF, Node generated JS/maps, tests and
documentation. These were not reset, cleaned, stashed or staged. The pre-existing
frontend `src/index.css` change is untouched. Node/Frontend checks use noEmit;
no compiled Node source was refreshed in this task.

The local Firebase runtime config, `.env.local` and `.secret.local` remain Git
ignored; no such contents were read. Tracked credential-like filename inventory
found only `functions/.env.example`. A filename-only-output signature scan of
184 changed source/artifact files found no recognized private-key/OpenAI/Google
key signatures. This is bounded static screening, not a proof that arbitrary
secrets cannot exist or approval to stage the entire historical tree. Existing
large changes still need human review in logical groups. Do not ship temporary
emulator normalization/bootstrap, fake identity injection, local counters or
private launching configuration as production configuration.

Suggested future commit groups: provider lifecycle/structured diagnostics;
Cost timeout/fencing; monetary/frontend/PDF contract; public auth/rules and
Node disable; durable persistence/service identity/dispatch; producer contract;
local validation tooling/docs. Separate generated JS/maps from source review
and reconcile build provenance. No commit was made.

## Remaining gates

| Item | Classification |
| --- | --- |
| Gemini quota unavailable; Timeline/Final reasoning unvalidated | MVP live blocker |
| TEST-008 budget not loaded/activated on current server | TEST-008 live blocker; not a new Timeline business-code failure |
| Explicit approval and fresh single-run preflight | Every live step |
| Native PDF dependencies/fonts in managed Linux, upload/sign permissions | Production-only; local binary tests pass |
| Real private delivery, certificate verification, IAM/OIDC, Cloud Tasks | Production-only; mocks/emulators do not prove platform security |
| Scanner scheduling and durable cursor, queue retry/rate policy, alerts | Production operational gate |
| Retention, cancellation/deletion, restore/recovery drills | Production operational/product gate |
| Signed/bearer download issuance, expiry and revocation | Production security/product gate |
| Concurrent jobs' shared project display selection | Product policy before concurrent production use |
| More than 400 ledger rows | Post-MVP scalability if MVP scope enforces cap; otherwise release blocker |

Do not remove the ledger cap or weaken fencing. Root authoritative money remains
base 27,942.71 + contingency 3,514.03 = 31,456.74. P50 29,283.60, P80 32,797.63,
P90 35,067.77 remain separate. TEST-008 must satisfy the monetary formula with
its actual generated values; do not force fixture totals onto live results.

Rollback: the budget is opt-in; future operator-controlled removal of the flag
restores original transport behavior after review and a deliberate restart.
Never disable it to continue the same partially consumed TEST-008. Preserve
counter/job evidence and use existing fenced recovery only under explicit
approval. No automatic destructive repair is provided.

## Validation record for this task

- Python: **465 distinct passing tests** (458-test combined run including 19
  budget tests, then seven additional budget cases; overlapping focused reruns
  are not counted again). The final 26 budget cases include concurrent threads,
  process persistence, endpoint rejection, wrapped SDK errors, health attestation
  no HTTP send after slot 20, and actual installed SDK wrapping/cleanup. A 74-test lifecycle/provider rerun and a
  92-test diagnostics/structured-error rerun passed.
- Node: **136**, including real cross-language producer/start contract, mocked
  pricing/identity/authorization and OpenAI disable.
- Frontend: **18**, routing/start/idempotency and authoritative money.
- Firestore emulator: **51**, isolated demo projects, native gRPC target fenced
  to 127.0.0.1:8081; fake provider/Cloud Tasks/identity verification boundaries.
- Rules: Firestore **30**, Storage **11** against isolated cached Storage 19199;
  all 41 passed. The isolated process was stopped after validation.
- Local PDF: **53** binary/HTML/monetary tests included in Python (35+18), all
  passed without dependency installation. Final's 23 cases are also included.
- **711 distinct passing tests in this task**, not added to historical totals.
- Python syntax/compile and Node/frontend TypeScript noEmit checks passed;
  git diff whitespace check passed. Prepared runners were statically checked,
  their fixture builders inspected offline, and their main functions not run.

Initial test setup failures were resolved without weakening checks: the new
hook initially passed a keyword to an old no-argument fake transport; an unset
flag now uses the exact original construction. Two Python tests require repo
root fixture paths; the combined rerun used repo root. Frontend needs its
existing test setup/mocks; the isolated config now includes that setup rather
than reading real Firebase environment. All affected reruns passed.

Network fences blocked Python sockets (and restricted emulator gRPC), Node
outbound sockets/DNS, and dotenv loading in offline launchers. Vite used
`envDir:false`. No dependency versions, provider credentials, service runtime
configuration, prompts, pricing mathematics or timeout policy were changed.
The signature scan found no known credential patterns, but does not replace a
human staged-diff review before any future commit.

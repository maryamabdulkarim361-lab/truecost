# Production blocker remediation V1

Offline implementation and local-emulator validation only. No live AI run, cloud API, deployment, IAM change, installation, credential creation or monetary formula change was performed. This supersedes the code-level Storage, Node CORS/authorization and application-error logging findings in Production Readiness V1. Production approval still requires the deployment actions below.

## Storage authorization

`collabcanvas/storage.rules` now applies the existing Firestore project policy:

- `projects/{projectId}/plans/**`: owner/editor read and write; viewer read; unrelated and anonymous users denied, including overwrite and delete.
- `construction-plans/{userId}/{fileName}`: only the path's authenticated UID. Current `FileUpload` uses this personal path. The unused optional `canvasId` overload in `uploadConstructionPlanImage` has no proven ownership relation and fails closed; do not use it for shared projects. Use the existing project-plan upload path instead.
- Generated reports (`pdfs/**`) and every other path: no browser read/write. Existing authorized Python PDF endpoints perform Admin writes and issue download links. Owner/editor permissions were not invented or broadened.

The tests prove **rules-enforced SDK access**, not revocation of bearer links. Existing `getDownloadURL` blueprint URLs can contain Firebase download tokens; those tokens can authorize access independently of rules if disclosed. PDF generation intentionally issues v4 signed URLs with a 3600-second lifetime. A signed-link recipient can use that capability until expiry. Before production, review/revoke legacy blueprint tokens and choose authenticated image delivery if the product requires identity on every image fetch. Do not claim that Security Rules alone revoke download tokens or signed links. No existing token, blob or signed URL was inspected or revoked in this task.

Production deployment must also grant/verify the Firebase Storage service agent's supported Firestore rules-lookup permission. Admin SDK artifacts bypass Storage Rules; server endpoint authorization and IAM remain essential.

### Isolated tests

The installed CLI resolves Storage `firestore.get` against its **launch project**, not the unit-test bucket project. Additionally, its `/internal/setRules` loader is global to that Storage emulator. The initial test against 9199 therefore loaded the new rules there but could not validate positive project membership using a different demo project. It did not modify production rules or application records. Final validation uses a separate demo emulator suite, which exits after testing; existing application services were not restarted.

`firebase.security-tests.json` selects localhost 18181/19199, separate hub/logging ports, and no Functions emulator. `storage.rules.test.ts` only targets those fixed local ports. Firestore rules tests accept only 127.0.0.1:8081 or 127.0.0.1:18181. Tests use fake users and isolated demo documents. The test preloader blocks non-loopback Node connections/DNS. Cached emulator JARs were used; Firebase CLI's attempted remote MOTD lookup was blocked before network access. No binaries were downloaded.

Reproduction requires existing cached emulator binaries. From `collabcanvas`, with the original services left running:

```sh
# Use a fresh temporary config directory so the CLI has no stored account data.
TEST_CONFIG_DIR="$(mktemp -d /private/tmp/truecost-rules-config.XXXXXX)"
CI=true FIREBASE_CLI_DISABLE_UPDATE_CHECK=true \
XDG_CONFIG_HOME="$TEST_CONFIG_DIR" \
NODE_OPTIONS="--require $(pwd)/functions/tests/offline-network.cjs" \
node node_modules/firebase-tools/lib/bin/firebase.js emulators:exec \
  --only firestore,storage --project demo-storage-remediation-v1 \
  --config firebase.security-tests.json \
  './functions/node_modules/.bin/vitest run --root functions src/storage.rules.test.ts src/security.rules.test.ts --silent'
```

Do not substitute a production project. If ports or cached binaries are unavailable, stop rather than download packages or stop other services.

## Every deployed Node endpoint

| Exports from `index.ts` | Classification and enforcement |
|---|---|
| `aiCommand`, `materialEstimateCommand`, `clarificationAgent`, `annotationCheckAgent` | Authenticated user-facing AI operations; verified UID; explicit project references and Firebase blueprint paths checked |
| `getHomeDepotPrice` | Authenticated user-facing pricing; global cache retains its established shared semantics |
| `sagemakerInvoke` | Authenticated project operation; owner/editor checked before credential/provider access |
| `estimationPipeline` | Authenticated legacy project operation; collaborator check corrected from `id` to existing `userId` + editor role |
| `comparePrices` | Authenticated project pricing operation; nested `request.projectId` checked before cache/provider/storage work |
| `triggerEstimatePipeline`, `updatePipelineStage` | Authenticated owner/editor operations; shared guard supplements existing checks |
| `sendContactEmail` | Existing public contact form, not a webhook; explicit origin policy, safe error response; remains intentionally unauthenticated |

No deployed webhook was found. The commented project-deletion trigger is not a processing worker.

`functionSecurity.ts` wraps Firebase's own `onCall`, preserving callable envelopes and SDK token verification. It rejects untrusted origins, missing authentication, body identity mismatches, invalid project references and cross-user access before entering handlers. Successful business results are unchanged. Authentication/authorization errors preserve callable error codes with fixed messages. `aiCommand` now derives its output UID from verified `request.auth.uid`.

All protected Node routes use configured `ALLOWED_WEB_ORIGIN` in production (managed `K_SERVICE` or explicit `APP_ENV=production`). Missing, wildcard, non-HTTPS or non-origin values fail closed. Local CORS permits only localhost/127.0.0.1 HTTP origins. The old `corsConfig.ts` delegates to the same policy. The public contact form uses the same origin validation, including preflight, without adding an invented login requirement. Non-browser clients without an Origin still require authentication for protected callables.

**Python comparison consequence:** its existing Node call does not carry verified Firebase authentication. It is now rejected, both locally and in production, rather than receiving an anonymous bypass. The existing optional batch-enrichment fallback handles this; no pricing timeout, retry, Cost budget, fencing or monetary formula changed. Restoring authenticated Python→Node enrichment needs a reviewed service-identity endpoint or authenticated delegation design. This is distinct from the already-prepared private Python A2A service identity. Browser owner/editor comparison remains supported. Do not forward a fabricated UID or introduce a loopback auth bypass.

## Sanitized diagnostics

Node `safeDiagnostics.ts` emits fixed source event labels, recognized error category/reason, numeric HTTP status and metrics, stage, and generated request UUID. It does not serialize arbitrary argument objects, error messages/bodies, headers, URLs or request narratives. All Node application console calls route through it; static public error messages replace provider exception text in response/persisted error paths. Synthetic-marker tests cover logs and the callable failure response. Normal successful model response content is not rewritten.

Python `config.safe_logging` configures the application structlog boundary with an allowlist. `safe_error_text` replaces exception stringification at persistence/fallback boundaries, while retaining known error codes, HTTP status, timeout/network categories and the exact fixed stale-attempt diagnostic. Provider `StructuredError` classification and serialization remain intact. Generic exception stacks, inputs, outputs, arbitrary feedback, URLs and headers are dropped before rendering. The verbose agent banner logger and direct orchestrator/Scope printing now emit metadata only. Approved Cost/Risk/Final arithmetic and generated output values are unchanged.

This covers application logging paths; deployment must not enable verbose SDK/proxy header/body dumps. Historical logs were not scrubbed, scanned for secret values or deleted. No logging change can retract previously emitted data.

## Exact production timeout risk

`functions/main.py` decorates `start_deep_pipeline` with `timeout_sec=540`. The handler uses `asyncio.run(_start_pipeline_async(...))`; that coroutine atomically creates the estimate and awaits `PipelineOrchestrator.run_pipeline` through all seven stages before returning. Node forwarding likewise waits for that HTTP result. A2A has a per-call 300-second timeout; individual calls and normal scorer/critic/retry work can exceed the entrypoint's aggregate nine-minute ceiling. Function termination is not proof of worker cancellation, and only Cost currently has the narrow attempt fencing introduced earlier.

Repository inspection found no deployed task queue, durable background dispatcher, processing Firestore trigger, or durable resume-by-stage worker. Persisted progress, helper coroutine calls and the disabled deletion trigger do not provide durable asynchronous execution. No thread, fire-and-forget asyncio task, queue or trigger was introduced.

Recommended future architecture (requires separate approval/configuration): authenticate/authorize → atomic estimate/job claim plus durable dispatch intent → return accepted estimate ID promptly → authenticated durable workers execute/checkpoint stages → persist terminal state. A managed task queue/dispatcher such as Cloud Tasks is a candidate cloud component, **not an existing capability or authorized provisioning action**. Dispatch/claim reconciliation, idempotent retries, leases/fencing for all stages, IAM/audiences, bounded stage budgets and recovery after worker loss must be designed together. Frontend Firestore progress subscription can remain, but the start-result contract must support acceptance rather than final completion; tests must cover dispatch failure, duplicate delivery and resume. Local synchronous execution can remain for development.

**Production timeout blocker remains.** No timeout values were increased.

## PDF and IAM

The repository still declares managed Python 3.12 Functions. It does not establish native Pango/GLib-GObject/Cairo/HarfBuzz/Fontconfig/fonts in the deployed image. Local 35 binary/HTML PDF generator tests and 18 monetary PDF tests pass, but that is not evidence of Linux managed-runtime availability. No installation or engine change was made. Native production packaging, Storage upload/signing permissions and private A2A IAM remain deployment-phase blockers.

## Validation and remaining work

Offline regressions cover actual protected Node exports, shared CORS/auth guards, public-contact preflight, fake-secret serialization, Storage/Firestore rules, Python auth, mocked seven-stage orchestration, structured LLM failures, lifecycle/provider compatibility, Cost fencing and PDF money/rendering. No Timeline-005, Final live or TEST-008 execution occurred.

Resolved in source: broad Storage SDK access, wildcard protected Node CORS, missing Node callable authentication/project checks, body UID trust, raw application exception/log paths.

Still required before production: reviewed/deployed rules and cross-service rules IAM; blueprint bearer-token/delivery migration decision; public-contact abuse controls appropriate to launch; authenticated Python pricing enrichment if enabled; durable pipeline execution; native PDF packaging; secrets/identity/invoker/signing configuration; restore/retention policy; deployed security and live-provider validation. Do not label a mocked/local pass as production approval.

### Final offline results

508 distinct tests passed: 343 Python, 119 Node, 26 frontend, 11 Storage rules and 9 Firestore rules. Python includes 50 LLM lifecycle/provider, 119 structured-failure/diagnostics/Timeline/JSON boundary tests, 19 Cost safety tests, 23 Final offline tests and 53 PDF tests. The mocked seven-stage wiring passed. Changed-Python compilation (23 files), Node TypeScript build and `git diff --check` passed. Tests asserting legacy wildcard CORS/raw error messages were updated to assert the new security contract, not disabled.

### Changed-file manifest for this remediation

- Rules/test configuration: `collabcanvas/storage.rules`, `collabcanvas/firebase.security-tests.json`.
- Shared Node code: `collabcanvas/functions/src/functionSecurity.ts`, `safeDiagnostics.ts`, `corsConfig.ts`.
- Node endpoints: `aiCommand.ts`, `materialEstimateCommand.ts`, `pricing.ts`, `sagemakerInvoke.ts`, `clarificationAgent.ts`, `estimationPipeline.ts`, `priceComparison.ts`, `annotationCheckAgent.ts`, `estimatePipelineOrchestrator.ts`, `sendContactEmail.ts` (all under `collabcanvas/functions/src`).
- Node helper logging: `annotationQuantifier.ts`, `enhancedCsiMapper.ts`, `enhancedInference.ts`, `globalMaterials.ts`, `projectDeletion.ts` in the same directory. Corresponding generated `lib/*.js` and maps were rebuilt.
- Node tests/support: `functionSecurity.test.ts`, `deployedAuthorization.test.ts`, `contactSecurity.test.ts`, `storage.rules.test.ts`, `security.rules.test.ts`, `integrationRouting.test.ts`, `priceComparison.test.ts`; `collabcanvas/functions/tests/offline-network.cjs`.
- Python shared logging/entrypoint: `functions/config/safe_logging.py`, `functions/main.py`, `functions/utils/agent_logger.py`.
- Python exception-summary call sites: `agents/base_agent.py`, `agents/orchestrator.py`, `agents/scorers/base_scorer.py`, `agents/critics/base_critic.py`, `agents/primary/{location,scope,code_compliance,cost,risk,final}_agent.py`; `services/{price_comparison_service,pdf_generator,serper_service,weather_service,bls_service,cost_data_service,firestore_service}.py`; `tools/price_tools.py`; `validators/clarification_validator.py` (all under `functions`).
- Python test: `functions/tests/unit/test_safe_logging.py`.
- Documentation: this file and the supersession note in `docs/setup/production-readiness-v1.md`.
- Temporary isolated emulator config/logs: `/private/tmp/truecost-blocker-rules-v1/` (no actual credentials). Existing unrelated source modifications, including frontend CSS and approved monetary implementation, were preserved.

## Autonomous completion update

Durable start/worker/scanner HTTP composition, code-only Cloud Tasks dispatch, service-authenticated pricing and stable frontend accepted-job handling are now implemented and tested offline/local. These supersede the earlier code-disconnection blockers, not the deployed verification gates. No cloud resources, credentials, IAM, services or approved monetary mathematics were changed. [Current remaining work](pre-live-validation-status.md) records the retained 400-row ledger limit, native PDF packaging blocker, Storage emulator test setup timeout, and separate live-validation requirements.

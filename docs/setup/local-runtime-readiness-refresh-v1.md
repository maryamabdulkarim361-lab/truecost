# Local runtime readiness refresh V1

Local-only refresh on 2026-09-20. Current runtime is ready for separately authorized Timeline-005; no live validation was executed. This supersedes the stale-process/absent-emulator NO-GO in the earlier pre-live gate for this running session only. Production deployment gates remain unchanged.

## Current evidence

- Python 5003: PID 3708, started 2026-09-20 05:22:43 (machine local time), after current main.py, durable_http.py and production boundary source timestamps. Existing Python 3.12.14 venv, reloader disabled; `/health` HTTP 200.
- The user restarted Python from the original private launching shell and attested: Gemini, model `gemini-3.6-flash`, credential present, emulator mode, Firestore 127.0.0.1:8081, Auth 127.0.0.1:9099, A2A standalone 5003 and synchronous local execution. The launcher requires the selected provider credential to be present. No credential or process environment dump was read; this configuration attestation combines the user's launch confirmation, startup checks, process time and non-provider behavior, not an invented runtime diagnostics endpoint.
- Existing Firestore Java process 34253 remained on 8081; it was not restarted.
- Auth 9099 and Functions 5001 are served by task-owned emulator process 6564, using installed Node v20.20.2. SDK discovery loaded current compiled definitions. `comparePrices` and `triggerEstimatePipeline` OPTIONS requests each return 204 without entering pricing/start handlers.
- Current worker attestation: Node v20.20.2, `disableOpenAIEffective=true`, actual compiled `isOpenAIEnabled()` returns false. That helper short-circuits before credential access. No key/sentinel was inspected. The four comparison OpenAI constructors remain guarded.
- Auth smoke: an isolated emulator user was created; unauthenticated Python `get_pipeline_status` returned 401; authenticated read of an owned temporary estimate returned 200 and the expected harmless marker from Firestore. Both user and document were deleted. Final smoke repeated successfully after emulator stabilization.
- `est-local-timeline-005` returned 404 in a read-only emulator check. It was not created. Timeline OPTIONS returned 204; no Timeline POST occurred.

## Local launch configuration and limitations

The normal repository firebase.json and package Node 20 declaration were preserved. Current TypeScript was compiled with installed Node 20 to refresh the existing lib artifacts. A verified Git-ignored `collabcanvas/firebase.runtime-refresh.local` copies repository topology and selects only the Node codebase; Python continues independently on 5003. This avoids starting an unnecessary Firebase Python worker. Existing local secret files were not read by the assistant or changed; the emulator uses its normal local resolution mechanism.

Temporary launch support is under `/private/tmp/truecost-runtime-refresh-*`, with a Node 20 wrapper in `/private/tmp/truecost-runtime-node20`. It blocks non-loopback DNS/socket networking, disables raw CLI debug-file logging, emits only fixed status categories, and confines discovery port probes to loopback. These controls are local process bootstrap configuration; no third-party files or application source were patched. Optional cloud configuration/secret lookups were blocked before external connections and produced unavailable-resolution warnings. This is not proof that optional search credentials are configured, nor authorization to fetch them.

Two local launch problems were corrected:

1. A temporary config outside the repository caused the CLI to join/validate source paths incorrectly. The final ignored config resides alongside firebase.json and uses the repository's relative Node source path.
2. Firebase's Node emulator sets `K_SERVICE`, which correctly trips the application's managed-production guard and rejects emulator flags. The **local bootstrap only**, after requiring an emulator worker, the exact local Auth host and a non-production APP_ENV, removes that managed-runtime marker before application import. Production source guards remain unchanged. Ordinary future emulator restarts without this local normalization may reproduce the failure; re-attest runtime readiness after any restart.

The final running Auth/Functions services remain available. Only task-owned startup attempts were restarted; the user's Firestore and refreshed Python were left intact. No services were deployed, cloud resources created, billing/IAM changed, or login performed.

## Reserved live-validation plan

Timeline-005 remains one isolated Timeline agent on standalone 5003, with existing fixtures, an atomic create-only fresh parent, one explicit execution, zero client/SDK retries, and no orchestrator/scorer/critic or other stages. Static source shows exactly one generate_json call; strict Timeline/JSON contracts and structured failures remain present. It has no Node pricing/OpenAI path. Maximum provider requests: 1. Presence attestation is not quota/authentication validation; no quota probe occurred.

Final-live and TEST-008 remain unexecuted and separately gated. Previously established TEST-008 budgets are unchanged: healthy first-pass maximum 15, conservative synchronous maximum 74, durable crash/redelivery allowance 222. Do not run the full pipeline as an uncontrolled follow-up to this refresh.

Validation performed: Node 20 TypeScript build; local OPTIONS/health/auth/status/Firestore smoke; static Timeline/SDK/reloader checks; local file ignore and diff checks. No agent prompts, provider behavior, Cost budgets/fencing or monetary calculations changed. No Gemini/OpenAI/external provider requests, real Cloud Tasks, cloud mutation, deployment, commit or push occurred.

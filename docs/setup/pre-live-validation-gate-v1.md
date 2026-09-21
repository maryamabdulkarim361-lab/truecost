# Pre-live validation gate V1 — continuation after 4B

Offline/local review on 2026-09-20. This is a NO-GO for execution until the runtime gates below are refreshed. No live provider request, reserved estimate creation, real queue request, deployment or IAM operation was performed. Section 4B remains PASS (140 previously passing relevant tests); its producer/consumer audit was not repeated.

## Storage rules

The user's current suite had no Storage listener on 9199, Auth listener on 9099 or Functions listener on 5001. Firestore 8081 was responsive. The historical setup timeout cannot be retrospectively attributed to a particular exception from the retained evidence.

An isolated Storage emulator used the already cached rules JAR, port 19199, the existing Firestore 8081 emulator and demo project `demo-storage-remediation-v1`. Downloads and outbound Node networking were blocked; no Functions were registered and no Auth emulator was required for rules-unit-testing's synthetic identities. Its temporary bootstrap initially omitted its own Storage registry entry, causing upload failure/retry; that diagnostic bootstrap was corrected without changing third-party code. Rules initialization and Firestore seed succeeded; the bounded test diagnostics pinpointed Storage upload as the stalled setup step. All 11 tests then passed. Rules were not weakened or deployed. The test-only process was stopped afterward; the user's services were not restarted.

The harness now bounds individual setup steps and disables seed-upload retries, so an unavailable Storage service cannot hide behind the previous 30-second hook timeout. Cross-user/anonymous blueprint reads, unauthorized writes/deletes, report SDK denial, and owner/editor/viewer behavior all passed. Already-issued bearer download links remain capabilities; rules do not revoke them.

## Runtime evidence and limits

- Python 5003 `/health`: HTTP 200. Process 27841 started September 19 at 23:11:24 local machine time; main.py and durable_http.py were modified afterward. Liveness does not prove the latest implementation is loaded. No restart performed.
- Firestore 8081: HTTP 200 and 51 actual emulator persistence tests passed.
- Auth 9099 and Functions 5001: connection unavailable. Storage 9199 also absent. The isolated Storage test success is not readiness of the user's full suite.
- Venv: Python 3.12.14; `pip check` passed. Installed dependency compatibility and local PDF imports/renders were exercised offline.
- Node: v22.20.0; the Functions package declares Node 20. TypeScript/tests pass locally, but this does not establish a Node 20 runtime match. Java: Temurin 21.0.12.1.
- Running-process provider/model, credential presence, A2A override, Auth emulator environment and execution mode are not exposed by `/health`. They were not recovered from process environment dumps or secret files. Current runtime values remain UNVERIFIED pending the launching-shell control-state confirmation. Historical Gemini configuration is not fresh runtime attestation.
- No Node Functions worker is listening, so an effective Node OpenAI disable flag cannot currently be attested. The four comparison guards pass offline tests. Local secret/config files were not read or changed.
- Intended local configuration: Gemini with `gemini-3.6-flash`; Python A2A on `http://127.0.0.1:5003/collabcanvas-dev/us-central1`; local comparePrices on port 5001; emulator mode effective; Auth emulator host 127.0.0.1:9099. These are requirements, not observed current environment values.

## Provider request budgets

Source: primary agents, base critic, deterministic scorers, `llm_service.py`, `orchestrator.py`, `durable_execution.py`. SDK retries remain zero. No standalone quota probe is needed or authorized.

| Stage | Maximum generations per primary execution with the 50-item kitchen fixture |
| --- | ---: |
| Location | 2 (search extraction, analysis) |
| Scope | 5 (three batches of at most 20 searchable names, one labor/material enrichment, one analysis) |
| Code Compliance | 2 (optional permit extraction, analysis) |
| Cost | 2 (complexity inference, analysis) |
| Risk | 2 (optional market extraction, analysis; Monte Carlo itself is deterministic) |
| Timeline | 1 |
| Final | 1 (recommendations; monetary mapping is deterministic) |
| Total | 15 |

Every scorer is deterministic (zero generations); each critic can generate once. With the default two quality retries, up to three successful/quality attempts plus one coordinated transient-provider retry give a conservative synchronous bound of `4 * 15 + 2 * 7 = 74` provider requests. The transient retry requires a safe delay no greater than 15 seconds; quota/auth errors which propagate structurally terminate rather than invoke scorer/critic. Some earlier agents still catch errors in optional enrichment and use fallbacks; they may proceed to another bounded generation. This known behavior was not broadly rewritten.

Durable execution defaults to up to three delivery attempts per operation, two quality retries, and one safe rate-limit retry per stage: a conservative crash/redelivery envelope is `3 * 74 = 222` requests. This is an upper bound, not an expected healthy run count. Lost acknowledgments can duplicate provider work before checkpoint publication even though fencing prevents stale authoritative writes. Scorer deliveries add zero provider requests. No unbounded loop or per-item LLM retry storm was found for this fixed fixture. Budgets require default retry settings and the unchanged fixed input; do not apply 74/222 to arbitrary-size user input.

Node comparePrices can independently call OpenAI through four paths. Each checks `isOpenAIEnabled()` before constructing a client; an effective `DISABLE_OPENAI=true` blocks all four irrespective of key presence. Other independently invoked Node AI functions are outside that switch and must not be invoked by these validation plans. Search/BLS/weather/pricing requests are distinct from Gemini generations and require their own scope in future live approval.

## Isolated Timeline-005 plan — not executed

Reserve `est-local-timeline-005`, JSON-RPC `timeline-validation-005`. Read-only emulator lookup returned 404; no parent was created. Adapt the existing reviewed `/private/tmp/truecost_timeline_004_runner.py` only when preparing the separately authorized executable runner. Use exactly the existing clarification from mock_risk_timeline_data and Location/Scope/Cost from mock_cost_estimate_data. Preserve historical fixture values. Atomic create-only parent, exact Firestore host, explicit execute-once, one POST to standalone `/a2a_timeline`, no proxy/redirect/retry, sanitized diagnostic output and evidence retention remain required.

The endpoint instantiates only TimelineAgent, whose task planner calls generate_json once. No orchestrator, scorer, critic or next stage is called. JSON must parse as an object; the Timeline task contract rejects null/noninteger/nonpositive durations and invalid task/dependency structures. Explicit insufficient_information is distinct from malformed JSON and provider failures. Maximum Gemini requests: **1**. Current server code/environment attestation must be refreshed before execution.

## Isolated Final-live plan — not executed

Reserve `est-local-final-001`, JSON-RPC `final-validation-001`; lookup returned 404. Use the existing complete input assembly in `functions/tests/unit/test_final_offline.py::inputs`: risk/clarification/timeline from mock_risk_timeline_data, Location/Scope/valid Cost from mock_cost_estimate_data, and its explicitly marked offline compliance stub. The test fixes the timeline fixture clock to 2025-01-18; preserve that existing test assembly rather than silently modernizing it. These are synthetic upstream results, not a claim that earlier live agents produced them.

A future runner must use the same localhost/emulator/create-only/single-post protections as Timeline and invoke only `/a2a_final`. It must not invoke start, scorer, critic or other stages. Final reads the parent and optional cost ledger; an empty emulator ledger uses the existing supplied Cost data. Only this fresh estimate's Final output/root is written. One recommendations generation, no SDK or application retry: maximum **1** request. No executor was run or document created here.

The shared fixture regression proves base 27942.71 + contingency 3514.03 = finalEstimate/totalCost 31456.74, with P50/P80/P90 29283.60/32797.63/35067.77 separate. Monetary calculations do not depend on generated recommendations. A successful Final result with fallback recommendations does not by itself prove a successful provider generation; retained safe generation/error evidence must be reviewed.

## TEST-008 plan — not executed

Minimum recommended MVP reasoning validation: **A, one legacy synchronous LOCAL pipeline**, after isolated Timeline and Final are satisfactory. Use `est-local-test-008`, the full kitchen JSON fixture, and exactly one authenticated standalone start POST. The parent lookup returned 404; no identity/document was consumed. Verify `PIPELINE_EXECUTION_MODE=synchronous` in this explicitly local run, matching authenticated emulator user/project ownership, A2A 5003, emulator-only pricing and effective Node OpenAI disable before submission. No automatic whole-pipeline retry. Observe through terminal state in the canonical order Location, Scope, Code Compliance, Cost, Risk, Timeline, Final.

This validates seven-stage reasoning with the existing local path; it does NOT validate production durable delivery. Durable persistence/HTTP/fencing already has zero-provider E2E coverage. Running both paths live now would duplicate quota consumption. A later durable-local live test requires an explicitly reviewed local-only dispatch driver; the production-oriented durable HTTP runtime constructs the real Cloud Tasks adapter and must NOT be selected as a supposedly offline queue emulator. Real cloud delivery remains a separately approved production gate.

For generated TEST-008 outputs, assert Final = authoritative base + exactly one contingency, totalCost = Final, location adjustment retained, percentiles separately sourced, and frontend/PDF agreement. Do not force live-generated costs to equal the fixed offline fixture amounts. The approved fixed monetary regression remains unchanged.

The kitchen fixture has exactly 50 CSI items. Scope enriches existing items without appending model-selected rows, so Cost's ledger remains below 400: **TEST-008 SAFE**. The >400 fail-closed cap is an application atomic-promotion budget and a production scalability/design limit, not a universal Firestore limit or a blocker for this fixture.

## Production/security and recovery review

Concrete correction: production Python, Node forwarding and frontend URL guards rejected a development project in the host but not in the path. They now also reject `collabcanvas-dev` in the path, with offline regression tests. No local routes or provider/monetary behavior changed.

Verified source/test boundaries: Firebase UID verification and project owner/editor authorization; body UID cannot establish identity; stable input fingerprint/idempotency conflict checks; durable 202 without inline execution; private worker/scanner/readiness declarations; Google issuer/exact audience/verified allowlisted service email/sub; exact identifier-only task envelope and current revision/generation gate; no anonymous production identity fallback; real dispatcher selected by production runtime rather than client-supplied factory; deterministic task names and 409 acceptance; bounded dispatch HTTP with no redirects; Node private pricing identity checked before processing. Deployment IAM and real Google certificate validation are not proved by injected offline verifier tests. Arbitrary production configuration correctness, service existence and credential validity are not established offline.

Pending outbox, queue failure, duplicate delivery, lost acknowledgment, crash/expired lease, stale writes, bounded scanner pages, terminal structured quota/auth/invalid response, Final acknowledgment loss and pricing fallback remain covered by offline/local tests. Scanner cursor persistence, queue settings, backlog monitoring, retention/restore drills, durable cancellation/deletion, and project-level active-estimate arbitration remain explicit operational/product gates. No destructive repair was added.

Local PDF binary/HTML/monetary regression passes. Managed Linux Python 3.12 native Pango/GLib/GObject/Cairo/HarfBuzz/Fontconfig/fonts and Storage upload/signing remain **UNVERIFIED — PRODUCTION-ONLY BLOCKER**. This does not block local Timeline/Final/TEST-008 reasoning validation.

## Validation record for this continuation

- Python: 439 distinct passing tests (438 combined plus one new production-path rejection case; 23-test production suite rerun passed).
- Firestore persistence/HTTP/fencing: 51 passed; Firestore rules: 30 passed.
- Storage rules: 11/11 passed on isolated Storage 19199; preceding setup failures not counted as passes.
- Node security/pricing/config: 130 distinct passing tests (129 combined plus new path rejection).
- Frontend: 21 distinct passing tests including new production-path rejection.
- Total: **682 distinct passing tests** in this continuation, including 53 local PDF tests within Python. Reruns are not double-counted. Prior 4B's 140 are preserved, not added again.
- No provider networking, real Cloud Tasks, live estimates, deployments or IAM changes. Temporary test-only Storage process stopped; user services unchanged.

GO/NO-GO: offline/security gates PASS; execution readiness **NO-GO** pending fresh runtime configuration attestation, current Python code reload, required Auth/Functions emulator readiness and Node disable-state confirmation. Do not infer readiness from a historical launching environment. No live test is authorized by this document.

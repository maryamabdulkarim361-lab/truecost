# Integration wiring V1 — offline audit

## Active paths

The estimate UI (`EstimatePage.tsx`) calls `pipelineService.triggerEstimatePipeline`
directly over HTTP to Python. It does **not** traverse the Node callable on this path.
Firebase SDK callables remain configured in `services/firebase.ts` for Node port 5001.
The exported Node `triggerEstimatePipeline` is a separate authenticated entry point;
it now requires completed `clarificationOutput` rather than fabricating an incompatible
project/location/scope object. It reports failed Python starts as errors.

| Boundary | Local target/configuration | Contract |
|---|---|---|
| React → Node | Firebase SDK; VITE_USE_FIREBASE_EMULATORS; 5001 | Firebase callable envelope/auth |
| React → Python | VITE_PYTHON_FUNCTIONS_URL; otherwise emulator default 5003 | POST start_deep_pipeline: userId, projectId, clarificationOutput with estimateId |
| Node → Python | PYTHON_FUNCTIONS_URL; FUNCTIONS_EMULATOR=true default 5003 | Same start body; userId from authenticated caller |
| Python → primary/scorer/critic | A2A_BASE_URL; emulator default 5003 | JSON-RPC message/send at a2a_{agent} |
| Cost → Node comparison | USE_FIREBASE_EMULATORS / emulator signals; 5001 | comparePrices; unchanged by this task |
| React → PDF | Same Python URL resolver; generate_pdf | estimate_id, client_ready, sections; snake-case response mapped to frontend |

VITE_FIREBASE_FUNCTIONS_URL no longer selects the Python pipeline. Explicit
Python URL overrides remain authoritative. No environment/secret file was read
or modified. Production URL branches remain separate from emulator defaults.
Non-emulator Python A2A's historical localhost:5001 fallback remains unchanged;
production requires an explicit A2A_BASE_URL. Runtime banners/docs mentioning
5002 as Python are historical; 5002 remains a legitimate Firebase Hosting port.

## Stages and persistence

Canonical agents: location → scope → code_compliance → cost → risk → timeline → final.
`cad_analysis` is an existing UI/Node preparation marker, not an eighth Python agent.
Python project sync now maps all seven names identically; Timeline no longer maps to Risk.

- estimates/{estimateId}: status (processing/final/failed), pipelineStatus, and root money.
- estimates/{estimateId}/agentOutputs/{agent}: output, score, completion metadata.
- Root agent fields retain existing `{agent}Output` spelling, including
  `code_complianceOutput`; no historical field migration is performed.
- projects/{projectId}/pipeline/status: currentStage, completedStages, pipelineId,
  progress, status (running/complete/error), timestamps.
- Node context: projects/{projectId}/pipeline/context; source board/state,
  scope/data and shapes remain project context, not replacement clarification.
- Frontend progress observes estimates/{estimateId}; completed status `final` maps
  to UI `complete`. PDF reads the same estimate identity and related report data.

## Offline evidence and limits

The integration test runs the real orchestrator, scorer decision flow, A2A HTTP
serialization/result extraction, Firestore service writes and Cost attempt fencing
against fake HTTP/Firestore boundaries. Primary responses use existing fixtures;
Final executes its real mapping with a fake LLM. All seven outputs, progress,
project-stage identities and approved root monetary fields are asserted.
This validates wiring, not live model quality, emulator networking or PDF binaries.

Security follow-up: Python start trusts body userId rather than validating the
optional frontend bearer token. PDF frontend omits auth and Python PDF handler
has no visible token/ownership enforcement. Node callable validates auth/project
access but does not forward caller credentials to Python. These policies were
not broadened or changed. Production authentication/authorization remains pending.

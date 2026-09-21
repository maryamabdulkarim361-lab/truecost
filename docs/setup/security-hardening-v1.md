# Security Hardening V1

## User-facing boundaries

`start_deep_pipeline`, `get_pipeline_status`, `delete_estimate`, and `generate_pdf`
use `services/request_auth.py`. POST requests require a Firebase ID token in
Authorization: Bearer. Firebase Admin verifies the token; the verified UID is
stored in a request-scoped ContextVar and reset after handling. Missing,
malformed, invalid and expired credentials produce sanitized 401 responses.
A supplied body userId must equal that UID. Resource denial is 403, absence 404,
invalid IDs 400. Authorization-store failure fails closed (503).

Project starts permit ownerId or an actual `{userId, role: 'editor'}` collaborator,
matching project types and shareProject writes. Viewers cannot start a pipeline.
Estimate read, deletion and PDF access remain **estimate userId owner-only**, as
in existing estimate rules. Project membership does not grant access to another
user's estimate. PDF `project_id` remains the existing compatibility alias for
an estimate document ID, not a way to authorize by a different project.
An existing estimate ID cannot be reused; creation is atomic/create-only on the
user-facing start path, preventing an authorization-check/create race overwrite.

Frontend pipeline/PDF services share token acquisition. Firestore realtime reads
use Firebase SDK authentication and Firestore rules, not an additional Python
status request. Token acquisition failure prevents the HTTP request. Node's callable
validates request.auth and forwards its original Authorization header; Python
independently verifies it. No shared secret is introduced.

## Emulator setup (not changed by this task)

For local signed-in users, configure Firebase Admin with
`FIREBASE_AUTH_EMULATOR_HOST=127.0.0.1:9099` and the matching project ID before
server startup. The frontend already uses the Auth emulator when emulator mode
is enabled. The emulator flag alone is **not** an authentication bypass; the SDK
must verify the supplied emulator-issued token. Never set Auth emulator settings
in production. No environment files or credentials were inspected or modified.

## Internal A2A

All deployed A2A decorators use invoker=private. Deployment requires an explicit
orchestrator service principal, invoker IAM grants, and audience-bound service ID
tokens in A2AClient. **Those infrastructure grants/token acquisition are not
implemented or deployed here. Production rollout is blocked until they are.**

The standalone Flask server remains bound to 127.0.0.1:5003. Internal non-browser
HTTP calls remain supported; browser Origin requests are rejected and malformed
JSON-RPC envelopes fail before agent/LLM allocation. This is defense in depth,
not authentication of local OS processes. Do not expose/proxy the standalone
server or treat it as a production security boundary. Do not publicly expose
A2A through the Firebase emulator. No secrets or emulator-mode bypass was added.

## Firestore policy and evidence

Project access now checks actual membership in the existing exact `{userId, role}`
array, rather than the mere existence of a collaborators list. Editors cannot
change collaborator permissions. Estimate child access checks the parent userId,
never a child-controlled userId. Rules tests use the existing localhost:8081
emulator and only `demo-security-hardening-v1`; production and normal development
project data/rules were not touched. Source rules have not been deployed.

The existing global boards, shared caches, chat-resource membership, and broader
client-write integrity policies require a separate rules review. This scoped
change is not a claim that all collections or all application endpoints are
production-hardened. Existing frontend query shapes were retained.

## Validation limits

Token verification is mocked offline (including failure paths). Actual HTTP handler
source is tested with provider/storage I/O mocked. Existing seven-stage mocked
orchestration, monetary/PDF HTML and async lifecycle suites cover regressions.
No Gemini/OpenAI request or AI pipeline was executed. Production IAM, deployed
rules, end-to-end emulator sign-in, and server reload remain separate rollout steps.

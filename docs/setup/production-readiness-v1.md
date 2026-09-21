# Production readiness V1

Follow-up: [Production Blocker Remediation V1](production-blocker-remediation-v1.md) supersedes the code-level Storage, Node CORS/authorization and application logging findings below. This document retains the original audit evidence; deployment, bearer-link, durable execution, PDF and IAM validation remain outstanding.

This is an offline preparation audit, **not permission to deploy or a production approval**.
No cloud resources, IAM, production records, credentials, billing, or services were changed.
Local tests do not establish live provider readiness. Timeline-005, Final live, and TEST-008 remain separate, explicitly authorized validations.

## Established architecture

| Component | Repository target | Status |
|---|---|---|
| React/Vite | Firebase Hosting `dist`, SPA rewrite in `collabcanvas/firebase.json` | Deployment configuration required |
| Node functions | Firebase Functions codebase `default`, Node 20, `collabcanvas/functions` | Existing declaration; security/deployment validation required |
| Python | Firebase Functions codebase `python`, Python 3.12, `../functions` | Existing declaration; not Flask production hosting |
| Firestore, Auth, Storage | Firebase services in an explicitly selected project | Production resources/rules/IAM unverified |
| PDF | Python `generate_pdf`, WeasyPrint, Firebase Storage upload and signed URL | Local binary rendering verified; runtime libraries/signing unverified |
| Primary/scorer/critic A2A | Private Python HTTP functions, `us-central1` | Private declaration and caller identity code prepared; IAM unverified |
| Standalone Flask | Loopback 5003, `use_reloader=False` | Development only; refuses managed/explicit production startup |

`.firebaserc` still selects **collabcanvas-dev** by default. Never deploy using that default as production. No production project identifier is asserted by this repository audit. Production deployment must explicitly select a reviewed project; no deployment command was run. The configuration guard rejects the development project in production.

The entrypoint `_start_pipeline_async` runs the entire pipeline synchronously. `start_deep_pipeline` has a 540-second function limit while individual A2A calls can each take 300 seconds plus internal retries. This is a **production duration/durability blocker**; the source itself notes a future durable task mechanism. This audit does not invent or provision that mechanism. Cost's 240-second and enrichment's 20-second budgets and attempt fencing are unchanged.

## Configuration separation

Python and Node detect production through `APP_ENV=production` or managed `K_SERVICE`. A managed service cannot disable checks by setting an emulator flag. Production deployment discovery/build must also explicitly set the reviewed non-secret configuration. Unmanaged runtimes must set `APP_ENV=production`; implicit non-managed mode remains development for compatibility.

Production Python requires:

- `FIREBASE_PROJECT_ID`: explicit non-development, non-demo project; platform project variables must agree.
- `A2A_BASE_URL`: explicit HTTPS function base, no local/IP host, non-443 port, credentials, query, fragment, or development project host.
- `ALLOWED_WEB_ORIGIN`: one explicit HTTPS browser origin, without path/query/credentials.
- `LLM_PROVIDER` and `LLM_MODEL`: reviewed selected provider/model, with the selected provider credential provisioned separately. This task does not change them.

Production Node requires an explicit non-development `GCLOUD_PROJECT`/`GOOGLE_CLOUD_PROJECT` and HTTPS `PYTHON_FUNCTIONS_URL`. Index initialization and the forwarding resolver validate configuration. No local OpenAI sentinel is copied or read by this task. `.env.local` and `.secret.local` are ignored by Git and excluded by the Functions `*.local` packaging ignore rule. Their contents must never be copied into deployment configuration.

Both backends reject emulator host variables, enabled `FUNCTIONS_EMULATOR`/`USE_FIREBASE_EMULATORS`, and enabled local `DISABLE_OPENAI` in production. This flag remains available locally; the existing four guarded Node pricing paths are unchanged. Other legacy Node OpenAI functions are independent of Python provider selection and must be deliberately included/excluded in the eventual production product.

The Vite production build requires a non-development Firebase project, complete public Firebase configuration, disabled emulators, and an explicit HTTPS `VITE_PYTHON_FUNCTIONS_URL` before SDK initialization. Actual Firebase public configuration/project ownership still needs deployment verification; checking nonempty fields is not proof of resource validity. Do not put any server credential in a `VITE_*` variable. Public Firebase client configuration is not a server secret.

Local defaults remain compatible: Python A2A 5003, Node comparison 5001 when emulator mode is effective, Firestore 8081, Auth 9099, Storage 9199. Legacy development-only URL defaults remain in resolvers, but managed/explicit production guards run before they can be selected. Production comparison derives its HTTPS target from the validated Firebase project; Python A2A never shares the Node emulator target.

## A2A trust boundary

All primary, scorer and critic deployment decorators use `invoker="private"`. The orchestrator's runtime service account is the expected caller. The A2A client obtains an OIDC ID token through Application Default Credentials, with the **exact target function URL as audience**, immediately before the request. Acquisition runs off the event loop; there is no stored shared secret, URL token, browser token or logged token. Failed acquisition fails the call; no anonymous fallback is attempted. Offline tests mock token acquisition and transport, including its failure path. No real token was issued.

The managed Functions/Cloud Run platform must verify the token and enforce invoker IAM before entering application code. Grant only the orchestrator's reviewed runtime identity invoke permission on the private targets (for gen2, underlying Cloud Run invoker semantics must be verified). Deploy each receiving service with its reviewed runtime identity and only required Firestore/secret permissions. Actual identity names, audiences, deployed URL form, and grants remain cloud configuration requirements. Test anonymous and wrong-identity rejection after deployment. Local standalone loopback is not production authentication and must not be publicly proxied.

Browser user endpoints remain protected by Firebase ID-token verification plus ownership/editor authorization. Node forwarding preserves that user token for independent Python verification. Private A2A uses service identity, not the user's Firebase token.

## HTTP, authorization and data trust

Python user endpoints enforce a configured production Origin and return the fixed allowed-origin header. Origin is a browser boundary, not authorization; non-browser requests still require verified identity. Unexpected errors at Python HTTP/A2A transport boundaries now use static messages rather than raw exception strings. Development Flask remains debug-enabled locally, but refuses production startup before enabling its emulator defaults.

**Remaining blockers:** Node callables still declare `cors: true`; the contact endpoint has its own CORS policy. A shared reviewed production browser-origin policy must cover all exported Node endpoints. Pricing `comparePrices` does not currently require caller identity/ownership before starting work. Legacy Node endpoints and `updatePipelineStage` need a privileged-operation authorization review. Restrict their deployment until resolved; CORS alone does not authorize service calls.

Firestore rules now deny client create/update/delete of root `estimates`, `agentOutputs`, and `costItems`. The current frontend only reads these records; authorized Python endpoints create/delete estimates, and Admin SDK workers write derived outputs. This protects money and pipeline status from owner forgery, not just cross-user forgery. Admin SDK bypasses Security Rules, so its application authorization and service-account IAM remain essential. Estimate conversations/version snapshots retain prior owner rules; these are not authoritative monetary outputs.

Owner/editor/viewer project rules remain intact. Project pipeline progress documents retain legacy editor write permissions; these are not trusted authoritative estimate results. Global material/product-cache and shared board collections have broader legacy permissions and require review before broad production exposure.

**Storage blocker:** `construction-plans/**` and `projects/{projectId}/plans/**` allow every authenticated user read/write. Project membership is not checked. This is not acceptable production isolation. A reviewed Storage ownership/membership policy and emulator tests are required. Generated PDFs are denied by client rules and uploaded with Admin credentials; signed URLs bypass client rules during their validity. Signed-URL access, expiry and signing permission require production validation.

No Firestore or Storage rules were deployed. Rules tests load only the isolated `demo-security-hardening-v1` project on the local emulator, never production or the local application's rules namespace.

## Secret names only

No credential value was inspected. Git's tracked-path inventory found `functions/.env.example` and secret-access source code, but no tracked `.env`, `.secret.local`, service-account credential or private-key file. This is **not an exhaustive content/history secret scan**. Do not interpret it as proof that the entire Git history is clean. Existing source references were audited by name; no client-side server-secret reference was identified in the reviewed integration paths.

| Name | Classification |
|---|---|
| `GEMINI_API_KEY` | Required if Gemini is the production provider; environment/secret injection needed |
| `OPENAI_API_KEY` | Required if Python OpenAI or retained Node OpenAI features are used; never a fallback from Gemini |
| `SERP_API_KEY` | Optional enrichment capability; fallbacks exist; required for enabled live retailer/search features |
| `SERPER_API_KEY` | Optional Serper integration; existing service also accepts `SERP_API_KEY`, a provider-credential ambiguity to review before enabling it |
| `VITE_GOOGLE_MAPS_API_KEY` | Browser Maps key, optional address autocomplete; requires browser/referrer/API restrictions, never substitute a server credential |
| `VITE_FIREBASE_API_KEY` | Public Firebase web configuration, not a server credential |
| `BLS_API_KEY` | Optional BLS capability; rate/quota behavior must be reviewed |
| ADC/runtime service identity | Required production Firebase/A2A credentials; prefer managed identity, not a key file |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional local ADC mechanism; no file inspected; not a proposed production key-file deployment |
| `RESEND_API_KEY` | Required only for enabled contact email integration |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Required only for retained SageMaker integration; optional to core Python seven-stage path |
| `SAGEMAKER_ENDPOINT_NAME`, `AWS_REGION` | Deployment identifiers represented as Firebase secrets; not credential values |
| local OpenAI sentinel, `DISABLE_OPENAI` | Local only; sentinel must never be injected into production |

Python Gemini reads environment; do not assume a Python secret is automatically bound merely because Node declares it. Python decorators currently lack explicit provider secret bindings. Bind selected secrets or review a supported environment-injection mechanism before deployment. Secret Manager project lookup no longer defaults to collabcanvas-dev. Missing values fail the selected LLM path rather than choosing another provider.

## Observability and health

Structured LLM diagnostics distinguish quota, transient rate limit, auth, timeout, invalid response, provider/transport failure, and genuine insufficient input. SDK retries remain zero; non-retryable provider errors bypass scorer/critic/quality retries. Request IDs, estimate IDs and agent names support correlation. HTTP authorization failures distinguish 401 and 403. Quality-validation failures remain separate.

**Log safety is not globally approved.** Safe LLM diagnostics do not retain provider bodies, prompts, credentials or arbitrary exceptions. However, legacy Node pricing/global-material paths log raw provider exceptions/bodies and request data; generic agent/storage error paths still stringify exceptions. These must be sanitized before deployment. This preparation sanitizes the Python entrypoint/A2A transport and Secret Manager failure paths only, without changing failure classification or business behavior.

`/health` belongs to `serve_local.py` and proves process liveness only. It does not prove Auth, Firestore, Storage, quotas, provider validity, or loaded source version. It is not an established production endpoint.

Production readiness should separately validate reviewed non-secret configuration; selected credential presence as boolean; a bounded Firestore service-identity read of a designated health record; and native PDF import/render capability in deployment validation. Health must never call an LLM or expose environment/token values. No production readiness endpoint or health record is provisioned here. Provider quota/validity needs separately authorized live validation, not recurring probes.

## PDF reproducibility

`functions/requirements.txt` records `weasyprint>=61.2,<62.0` and `pydyf>=0.10.0,<0.11`. Local binary tests pass with these installed packages. Native production dependencies include Pango, GLib/GObject, Cairo, HarfBuzz, Fontconfig, their transitive libraries, and fonts.

`firebase.json` declares managed Python 3.12 Functions, not a repository-controlled container. No checked-in Dockerfile/native-library installation step was found. Availability and ABI/font behavior in the actual build/runtime are unverified: **deployment blocker**. Do not assume Homebrew-installed Intel macOS libraries exist in Linux Functions. Validate the supported managed image or explicitly decide on a different supported packaging architecture; this task invents neither a container deployment nor a system package installation.

Production Storage bucket configuration, upload IAM and `generate_signed_url` signing capability also require validation. Monetary contracts, PDFs' values, and Cost/Risk mathematics were not changed.

## Backup, retention and deletion

No authoritative production Firestore backup/PITR/export schedule or Storage retention policy was found. Decide retention and deletion obligations with the product/data owner before configuring jobs; no period is invented here. Define backup scope, restore verification, access control, blueprint retention, PDF expiry/deletion, and orphan cleanup.

`FirestoreService.delete_estimate` deletes enumerated subcollections and parent records; it does not establish deletion of all associated Storage objects or arbitrary nested future subcollections. The Node project deletion trigger is commented out in `index.ts`. Review deletion semantics and a restore/recovery exercise before production. No backup job or data deletion was performed by this audit.

## Checklist

### READY (offline preparation only)
- [x] Explicit production configuration guards and local compatibility tests.
- [x] Private A2A decorators, service-identity acquisition path and mocked failure-closed tests.
- [x] Firebase ID-token/ownership regressions; no authentication bypass added.
- [x] Authoritative estimate outputs deny direct client writes; isolated emulator rules regression.
- [x] Mocked seven-stage wiring, approved money, local PDF rendering, Cost fencing and LLM lifecycle regressions.
- [x] Required PDF Python version bounds recorded.

### REQUIRES LIVE VALIDATION
- [ ] Timeline-005 once quota permits, under separate authorization.
- [ ] Final live validation and eventual controlled full pipeline, separately authorized.
- [ ] Selected provider behavior/quotas; no fallback to another provider assumed.

### REQUIRES CLOUD/IAM CONFIGURATION
- [ ] Select actual production project and domain explicitly, separate from `.firebaserc` development default.
- [ ] Runtime service identities, least privilege, private A2A invoker grants and validated audiences.
- [ ] Provider/search/email/optional AWS secret provisioning and bindings.
- [ ] Storage bucket/upload/signing identity, Firestore/Auth configuration.
- [ ] Reviewed backup/retention and deletion policy; restore exercise.

### REQUIRES DEPLOYMENT VALIDATION
- [ ] Resolve synchronous pipeline duration/durability before production traffic.
- [ ] Restrict all Node browser origins and validate sensitive callable authorization.
- [ ] Fix Storage cross-user access policy; review broader shared collections/progress writes.
- [ ] Sanitize remaining raw exception/body logging.
- [ ] Native PDF library/font packaging and production binary rendering.
- [ ] Deploy reviewed rules; verify real identities, unauthorized rejection, same-project routing and CORS.
- [ ] Verify production readiness checks and signed PDF access lifecycle.
- [ ] Secret-content/history scanning under an approved tool/process; this audit only inspected names/paths.

### DEFERRED POST-MVP
- [ ] Richer dashboards/alerts and operational analytics, after minimum safe failure observability.
- [ ] Advanced provider capacity planning and performance optimization.

Security, backups, deployment library availability and durable execution are **not** deferred merely to label the MVP ready.

## Validation evidence for this preparation

363 distinct tests passed after correcting two stale settings tests (they attempted to assign a read-only property; now use fake cached/mock values). Counts: 309 Python tests, 26 frontend tests, 19 Node unit tests, 9 isolated local Firestore rules tests. Python includes 35 PDF generator tests plus 18 monetary PDF tests, 50 LLM lifecycle/provider tests, 67 structured failure/diagnostic tests, and 19 Cost execution safety tests. Socket-blocked test batches and mocked provider/transports were used; rules tests made localhost emulator calls only. No LLM or external API calls, no real service tokens, no deployment.

Node TypeScript compilation and changed-Python `py_compile` pass. Generated Node `lib` artifacts correspond to source changes; unrelated existing modifications are retained. `git diff --check` passes. The nine rules tests were initially blocked by the filesystem/network sandbox's localhost restriction and then passed with permission limited to the local emulator; no cloud rules were loaded.

## Autonomous completion update

Current code preparation now includes durable public start, private worker/scanner/readiness boundaries, a Cloud Tasks REST adapter, and private Python-to-Node pricing OIDC integration. Earlier statements that these code boundaries do not exist are superseded; **cloud validation, IAM and deployment are still not complete**. Readiness checks do not contact a provider or assert connectivity. See [current remaining work](pre-live-validation-status.md) before any live or cloud action. Production PDF native packaging and Storage rules rerun remain blockers.

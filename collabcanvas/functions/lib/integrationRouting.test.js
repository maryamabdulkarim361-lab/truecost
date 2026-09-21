"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const estimatePipelineOrchestrator_1 = require("./estimatePipelineOrchestrator");
(0, vitest_1.afterEach)(() => vitest_1.vi.unstubAllEnvs());
(0, vitest_1.it)('routes emulator forwarding to Python 5003', () => {
    vitest_1.vi.stubEnv('FUNCTIONS_EMULATOR', 'true');
    vitest_1.vi.stubEnv('PYTHON_FUNCTIONS_URL', '');
    (0, vitest_1.expect)((0, estimatePipelineOrchestrator_1.getPythonPipelineUrl)()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline');
});
vitest_1.it.each(['http://127.0.0.1:5003', 'http://127.0.0.1:5003/collabcanvas-dev/us-central1/'])('normalizes %s', url => {
    vitest_1.vi.stubEnv('PYTHON_FUNCTIONS_URL', url);
    (0, vitest_1.expect)((0, estimatePipelineOrchestrator_1.getPythonPipelineUrl)()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline');
});
const state = vitest_1.vi.hoisted(() => ({ writes: [] }));
vitest_1.vi.mock('firebase-functions/v2/https', () => ({ onCall: (_options, handler) => handler, HttpsError: class extends Error {
        constructor(code, message) { super(message); this.code = code; }
    } }));
vitest_1.vi.mock('firebase-admin/app', () => ({ getApps: () => [{}], initializeApp: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-admin/firestore', () => {
    const ref = { collection: () => ref, doc: () => ref, get: async () => ({ exists: true, data: () => ({ ownerId: 'user', name: 'Fixture' }), forEach: () => { } }), set: async (data) => { state.writes.push(data); }, update: async (data) => { state.writes.push(data); } };
    return { getFirestore: () => ref, FieldValue: { serverTimestamp: () => 0 } };
});
const estimatePipelineOrchestrator_2 = require("./estimatePipelineOrchestrator");
const invoke = estimatePipelineOrchestrator_2.triggerEstimatePipeline;
(0, vitest_1.it)('requires completed clarification', async () => {
    await (0, vitest_1.expect)(invoke({ auth: { uid: 'user' }, rawRequest: { headers: { authorization: 'Bearer offline-test-token' } }, data: { projectId: 'project' } })).rejects.toMatchObject({ code: 'invalid-argument' });
});
vitest_1.it.each([false, true])('rejects failed Python start (%s)', async (networkFailure) => {
    vitest_1.vi.stubGlobal('fetch', vitest_1.vi.fn(async () => {
        if (networkFailure)
            throw new Error('offline failure');
        return { ok: false, json: async () => ({ success: false, error: { message: 'Invalid' } }) };
    }));
    await (0, vitest_1.expect)(invoke({ auth: { uid: 'user' }, rawRequest: { headers: { authorization: 'Bearer offline-test-token' } }, data: { projectId: 'project', clarificationOutput: { projectBrief: { projectType: 'kitchen_remodel' } } } })).rejects.toMatchObject({ code: 'internal' });
    (0, vitest_1.expect)(state.writes).toContainEqual(vitest_1.expect.objectContaining({ status: 'error' }));
    vitest_1.vi.unstubAllGlobals();
});
(0, vitest_1.it)('forwards authenticated identity and completed clarification', async () => {
    vitest_1.vi.stubGlobal('fetch', vitest_1.vi.fn(async () => ({ ok: true, json: async () => ({ success: true }) })));
    const result = await invoke({ auth: { uid: 'user' }, rawRequest: { headers: { authorization: 'Bearer offline-test-token' } }, data: { projectId: 'project', clarificationOutput: { projectBrief: { projectType: 'kitchen_remodel' } } } });
    const body = JSON.parse(vitest_1.vi.mocked(fetch).mock.calls[0][1].body);
    (0, vitest_1.expect)(Boolean(vitest_1.vi.mocked(fetch).mock.calls[0][1].headers.Authorization)).toBe(true);
    (0, vitest_1.expect)(body.userId).toBe('user');
    (0, vitest_1.expect)(body.clarificationOutput.estimateId).toBe(result.pipelineId);
    (0, vitest_1.expect)(body.clarificationOutput.projectBrief.projectType).toBe('kitchen_remodel');
    vitest_1.vi.unstubAllGlobals();
});
(0, vitest_1.it)('production forwards 202 without legacy status writes', async () => {
    for (const key of ['FUNCTIONS_EMULATOR', 'USE_FIREBASE_EMULATORS', 'DISABLE_OPENAI', 'FIRESTORE_EMULATOR_HOST', 'FIREBASE_AUTH_EMULATOR_HOST', 'FIREBASE_STORAGE_EMULATOR_HOST', 'STORAGE_EMULATOR_HOST', 'FIREBASE_DATABASE_EMULATOR_HOST'])
        vitest_1.vi.stubEnv(key, '');
    vitest_1.vi.stubEnv('APP_ENV', 'production');
    vitest_1.vi.stubEnv('GCLOUD_PROJECT', 'production-project');
    vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', 'https://app.example.com');
    vitest_1.vi.stubEnv('PYTHON_FUNCTIONS_URL', 'https://python.example.com');
    state.writes = [];
    vitest_1.vi.stubGlobal('fetch', vitest_1.vi.fn(async () => ({ ok: true, status: 202, json: async () => ({ success: true, data: { estimateId: 'server-est', jobId: 'job', status: 'accepted' } }) })));
    const result = await invoke({ auth: { uid: 'user' }, rawRequest: { headers: { authorization: 'Bearer offline-test-token' } }, data: { projectId: 'project', clarificationOutput: { estimateId: 'stable', projectBrief: { projectType: 'kitchen_remodel' } } } });
    (0, vitest_1.expect)(result.pipelineId).toBe('server-est');
    (0, vitest_1.expect)(state.writes).toEqual([]);
    (0, vitest_1.expect)(JSON.parse(vitest_1.vi.mocked(fetch).mock.calls[0][1].body).idempotencyKey).toBe('stable');
    vitest_1.vi.unstubAllGlobals();
});
//# sourceMappingURL=integrationRouting.test.js.map
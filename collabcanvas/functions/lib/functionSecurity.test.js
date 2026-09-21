"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const node_net_1 = require("node:net");
const state = vitest_1.vi.hoisted(() => ({ project: { ownerId: 'owner', collaborators: [{ userId: 'editor', role: 'editor' }, { userId: 'viewer', role: 'viewer' }] }, reads: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-functions/v2/https', () => ({ onCall: (_options, handler) => handler, HttpsError: class extends Error {
        constructor(code, message) { super(message); this.code = code; }
    } }));
vitest_1.vi.mock('firebase-admin/app', () => ({ getApps: () => [{}], initializeApp: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-admin/firestore', () => ({ getFirestore: () => ({ collection: () => ({ doc: () => ({ get: async () => { state.reads(); return { exists: true, data: () => state.project }; } }) }) }) }));
const functionSecurity_1 = require("./functionSecurity");
const safeDiagnostics_1 = require("./safeDiagnostics");
(0, vitest_1.beforeEach)(() => {
    vitest_1.vi.stubEnv('APP_ENV', 'production');
    vitest_1.vi.stubEnv('K_SERVICE', '');
    vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', 'https://app.example.com');
    const blocked = () => { throw new Error('Network forbidden'); };
    vitest_1.vi.spyOn(node_net_1.Socket.prototype, 'connect').mockImplementation(blocked);
    vitest_1.vi.stubGlobal('fetch', blocked);
    state.reads.mockClear();
});
(0, vitest_1.afterEach)(() => { vitest_1.vi.restoreAllMocks(); vitest_1.vi.unstubAllGlobals(); vitest_1.vi.unstubAllEnvs(); });
const req = (uid = 'owner', origin = 'https://app.example.com', data = { projectId: 'p' }) => ({ auth: uid ? { uid } : undefined, data, rawRequest: { headers: { origin } } });
(0, vitest_1.it)('fails closed when production origin missing', () => { vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', ''); (0, vitest_1.expect)(() => (0, functionSecurity_1.allowedOrigins)()).toThrow(); });
vitest_1.it.each(['*', 'http://app.example.com', 'https://app.example.com/path', 'https://localhost'])('rejects invalid production origin %s', origin => { vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', origin); (0, vitest_1.expect)(() => (0, functionSecurity_1.allowedOrigins)()).toThrow(); });
(0, vitest_1.it)('allows configured production origin', async () => { (0, vitest_1.expect)(await (0, functionSecurity_1.authorizeRequest)(req())).toBe('owner'); });
(0, vitest_1.it)('rejects other origin before storage access', async () => { await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req('owner', 'https://evil.example'))).rejects.toMatchObject({ code: 'permission-denied' }); (0, vitest_1.expect)(state.reads).not.toHaveBeenCalled(); });
(0, vitest_1.it)('requires auth even without browser Origin', async () => { await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)({ data: {}, rawRequest: { headers: {} } })).rejects.toMatchObject({ code: 'unauthenticated' }); });
vitest_1.it.each(['owner', 'editor'])('preserves project writer %s', async (uid) => { (0, vitest_1.expect)(await (0, functionSecurity_1.authorizeRequest)(req(uid))).toBe(uid); });
vitest_1.it.each(['viewer', 'attacker'])('rejects cross-user/non-editor action %s', async (uid) => { await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req(uid))).rejects.toMatchObject({ code: 'permission-denied' }); });
(0, vitest_1.it)('does not trust body userId', async () => { await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req('owner', undefined, { userId: 'attacker' }))).rejects.toMatchObject({ code: 'permission-denied' }); });
(0, vitest_1.it)('nested comparison request requires project authorization', async () => { await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req('attacker', undefined, { request: { projectId: 'p' } }))).rejects.toMatchObject({ code: 'permission-denied' }); });
(0, vitest_1.it)('auth failure never reaches provider handler', async () => {
    const provider = vitest_1.vi.fn();
    const handler = (0, functionSecurity_1.onCall)({}, provider);
    await (0, vitest_1.expect)(handler({ data: {}, rawRequest: { headers: {} } })).rejects.toMatchObject({ code: 'unauthenticated' });
    (0, vitest_1.expect)(provider).not.toHaveBeenCalled();
});
(0, vitest_1.it)('keeps callable success semantics', async () => {
    const handler = (0, functionSecurity_1.onCall)({}, async () => ({ success: true }));
    (0, vitest_1.expect)(await handler(req())).toEqual({ success: true });
});
(0, vitest_1.it)('does not expose raw error metadata or messages', async () => {
    const marker = 'SYNTHETIC_SECRET_MARKER';
    const log = vitest_1.vi.spyOn(console, 'log').mockImplementation(() => { });
    const err = Object.assign(new Error(marker), { status: 503, body: { key: marker }, headers: { Authorization: marker }, url: `https://example.com?key=${marker}` });
    (0, safeDiagnostics_1.safeLog)('provider.failure', err, { request_id: '12345678-1234-1234-1234-123456789012' }, marker);
    (0, vitest_1.expect)(JSON.stringify(log.mock.calls)).not.toContain(marker);
    (0, vitest_1.expect)(JSON.stringify(log.mock.calls)).toContain('503');
    const handler = (0, functionSecurity_1.onCall)({}, async () => { throw err; });
    await (0, vitest_1.expect)(handler(req())).rejects.toMatchObject({ code: 'internal', message: 'Operation failed' });
    (0, vitest_1.expect)(JSON.stringify(log.mock.calls)).not.toContain(marker);
});
vitest_1.it.each([401, 403, 429, 500, 503])('preserves safe HTTP status %s', status => { (0, vitest_1.expect)((0, safeDiagnostics_1.safeDiagnostics)({ status, message: 'SYNTHETIC_SECRET_MARKER' })).toMatchObject({ http_status: status }); });
(0, vitest_1.it)('rejects another users personal blueprint URL', async () => {
    await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req('owner', undefined, { planImageUrl: 'https://firebasestorage.googleapis.com/v0/b/fake/o/construction-plans%2Fattacker%2Fplan' }))).rejects.toMatchObject({ code: 'permission-denied' });
});
(0, vitest_1.it)('checks project ownership from blueprint URL', async () => {
    await (0, vitest_1.expect)((0, functionSecurity_1.authorizeRequest)(req('attacker', undefined, { planImageUrl: 'https://firebasestorage.googleapis.com/v0/b/fake/o/projects%2Fp%2Fplans%2Fplan' }))).rejects.toMatchObject({ code: 'permission-denied' });
});
//# sourceMappingURL=functionSecurity.test.js.map
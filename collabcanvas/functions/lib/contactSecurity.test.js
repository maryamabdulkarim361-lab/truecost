"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
vitest_1.vi.mock('firebase-functions', () => ({ region: () => ({ runWith: () => ({ https: { onRequest: (handler) => handler } }) }) }));
vitest_1.vi.mock('resend', () => ({ Resend: class {
        constructor() { throw new Error('Provider construction forbidden'); }
    } }));
const sendContactEmail_1 = require("./sendContactEmail");
(0, vitest_1.afterEach)(() => vitest_1.vi.unstubAllEnvs());
const response = () => { const res = { set: vitest_1.vi.fn(), status: vitest_1.vi.fn(), send: vitest_1.vi.fn(), json: vitest_1.vi.fn() }; for (const fn of Object.values(res))
    fn.mockReturnValue(res); return res; };
(0, vitest_1.it)('allows configured contact preflight without email/API call', async () => {
    vitest_1.vi.stubEnv('APP_ENV', 'production');
    vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', 'https://app.example.com');
    const res = response();
    await sendContactEmail_1.sendContactEmail({ method: 'OPTIONS', headers: { origin: 'https://app.example.com' } }, res);
    (0, vitest_1.expect)(res.status).toHaveBeenCalledWith(204);
    (0, vitest_1.expect)(res.set).toHaveBeenCalledWith(vitest_1.expect.objectContaining({ 'Access-Control-Allow-Origin': 'https://app.example.com' }));
});
(0, vitest_1.it)('rejects disallowed contact origin before provider access', async () => {
    vitest_1.vi.stubEnv('APP_ENV', 'production');
    vitest_1.vi.stubEnv('ALLOWED_WEB_ORIGIN', 'https://app.example.com');
    const res = response();
    await sendContactEmail_1.sendContactEmail({ method: 'POST', headers: { origin: 'https://evil.example' }, body: {} }, res);
    (0, vitest_1.expect)(res.status).toHaveBeenCalledWith(403);
});
//# sourceMappingURL=contactSecurity.test.js.map
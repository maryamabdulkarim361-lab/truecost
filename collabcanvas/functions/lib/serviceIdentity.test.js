"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const serviceIdentity_1 = require("./serviceIdentity");
(0, vitest_1.afterEach)(() => vitest_1.vi.unstubAllEnvs());
function setup() {
    vitest_1.vi.stubEnv('PRICING_SERVICE_URL', 'https://pricing.example.com/comparePricesService');
    vitest_1.vi.stubEnv('PRICING_SERVICE_INVOKER', 'worker@project.iam.gserviceaccount.com');
    return { iss: 'https://accounts.google.com', aud: 'https://pricing.example.com/comparePricesService',
        email: 'worker@project.iam.gserviceaccount.com', email_verified: true, sub: '123', iat: 1, exp: 9999999999 };
}
(0, vitest_1.describe)('private pricing identity', () => {
    (0, vitest_1.it)('accepts verified service only', async () => { const c = setup(); await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)('Bearer fake', async () => c)).resolves.toBeUndefined(); });
    for (const key of ['aud', 'email', 'iss', 'sub'])
        (0, vitest_1.it)(`rejects wrong ${key}`, async () => {
            const c = setup();
            await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)('Bearer fake', async () => (Object.assign(Object.assign({}, c), { [key]: key === 'sub' ? '' : 'wrong' })))).rejects.toThrow();
        });
    (0, vitest_1.it)('rejects unverified email', async () => { const c = setup(); await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)('Bearer fake', async () => (Object.assign(Object.assign({}, c), { email_verified: false })))).rejects.toThrow(); });
    (0, vitest_1.it)('rejects missing auth before verifier', async () => { setup(); const v = vitest_1.vi.fn(); await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)(undefined, v)).rejects.toThrow(); (0, vitest_1.expect)(v).not.toHaveBeenCalled(); });
    (0, vitest_1.it)('rejects localhost', async () => { setup(); vitest_1.vi.stubEnv('PRICING_SERVICE_URL', 'http://127.0.0.1:5001'); await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)('Bearer fake', vitest_1.vi.fn())).rejects.toThrow(); });
    (0, vitest_1.it)('rejects missing configuration', async () => { vitest_1.vi.stubEnv('PRICING_SERVICE_URL', ''); await (0, vitest_1.expect)((0, serviceIdentity_1.verifyPricingService)('Bearer fake', vitest_1.vi.fn())).rejects.toThrow(); });
});
//# sourceMappingURL=serviceIdentity.test.js.map
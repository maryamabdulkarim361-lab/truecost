"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const node_net_1 = require("node:net");
const openai_1 = require("openai");
const openaiAvailability_1 = require("./openaiAvailability");
const priceComparison_1 = require("./priceComparison");
const globalMaterials_1 = require("./globalMaterials");
const mocks = vitest_1.vi.hoisted(() => ({ create: vitest_1.vi.fn() }));
vitest_1.vi.mock('openai', () => ({
    default: vitest_1.vi.fn(function () {
        return { chat: { completions: { create: mocks.create } } };
    }),
}));
vitest_1.vi.mock('firebase-admin', () => ({ app: vitest_1.vi.fn(), initializeApp: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-admin/firestore', () => ({ getFirestore: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-functions/v2/https', () => ({
    onRequest: vitest_1.vi.fn((_config, handler) => handler),
    onCall: vitest_1.vi.fn((_config, handler) => handler),
    HttpsError: class extends Error {
    },
}));
// Retain only a reference to the original environment. Never read/save a real
// credential, even for restoration; all access to these two names is intercepted.
const originalEnv = process.env;
let fakeEnv = {};
(0, vitest_1.beforeAll)(() => {
    process.env = new Proxy(originalEnv, {
        get(target, property) {
            if (property === 'OPENAI_API_KEY' || property === 'DISABLE_OPENAI') {
                return fakeEnv[property];
            }
            return Reflect.get(target, property);
        },
    });
    const blocked = () => { throw new Error('Network forbidden in offline OpenAI guard tests'); };
    vitest_1.vi.spyOn(globalThis, 'fetch').mockImplementation(blocked);
    vitest_1.vi.spyOn(node_net_1.Socket.prototype, 'connect').mockImplementation(blocked);
});
(0, vitest_1.afterAll)(() => {
    process.env = originalEnv;
    vitest_1.vi.restoreAllMocks();
});
(0, vitest_1.beforeEach)(() => {
    fakeEnv = {};
    vitest_1.vi.mocked(openai_1.default).mockClear();
    mocks.create.mockReset();
    mocks.create.mockResolvedValue({ choices: [{ message: { content: JSON.stringify({
                        index: 0, confidence: 0.9, reasoning: 'offline match', aliases: ['cabinet'], description: 'Cabinet',
                    }) } }] });
});
const candidate = {
    id: 'cabinet', name: 'Cabinet', normalizedName: 'cabinet', description: 'Cabinet',
    aliases: ['cabinet'], zipCode: '80202', retailers: {}, createdAt: 0,
    updatedAt: 0, matchCount: 0, source: 'seed',
};
async function exerciseFallbacks() {
    (0, vitest_1.expect)(await (0, priceComparison_1.selectBestMatch)('Cabinet', [{}], 'homeDepot')).toEqual({
        index: 0, confidence: 0.5, reasoning: 'OpenAI not configured - defaulting to first result',
    });
    (0, vitest_1.expect)(await (0, priceComparison_1.generateProductMetadata)('Cabinet', '  BASE CABINET  ')).toEqual({
        aliases: ['base cabinet'], description: 'Cabinet',
    });
    (0, vitest_1.expect)(await (0, globalMaterials_1.selectBestGlobalMatch)('Cabinet', [candidate, Object.assign(Object.assign({}, candidate), { id: 'other' })])).toEqual({
        candidate, confidence: 0.5, reasoning: 'OpenAI not configured',
    });
    (0, vitest_1.expect)(await (0, globalMaterials_1.validateGlobalMatch)('Cabinet', candidate)).toEqual({
        confidence: 0.95, reasoning: 'Word overlap fallback (100% match)',
    });
    // Single-candidate routing also reaches the guarded validation fallback.
    (0, vitest_1.expect)((await (0, globalMaterials_1.selectBestGlobalMatch)('Cabinet', [candidate])).confidence).toBe(0.95);
    (0, vitest_1.expect)(openai_1.default).not.toHaveBeenCalled();
    (0, vitest_1.expect)(mocks.create).not.toHaveBeenCalled();
    (0, vitest_1.expect)(globalThis.fetch).not.toHaveBeenCalled();
    (0, vitest_1.expect)(node_net_1.Socket.prototype.connect).not.toHaveBeenCalled();
}
(0, vitest_1.describe)('comparison OpenAI kill switch', () => {
    vitest_1.it.each(['true', 'TRUE', '1', 'yes', 'YES', 'on', 'ON', ' True '])('disables all four paths for flag %s without even reading the key', async (flag) => {
        fakeEnv = { DISABLE_OPENAI: flag };
        Object.defineProperty(fakeEnv, 'OPENAI_API_KEY', {
            get() { throw new Error('Disabled paths must not read a credential'); },
        });
        await exerciseFallbacks();
    });
    vitest_1.it.each(['sk-proj-offline-fabricated-test-key', 'offline-sentinel', undefined])('does not construct a client with disabled key variant %#', async (key) => {
        fakeEnv = { DISABLE_OPENAI: 'true', OPENAI_API_KEY: key };
        await exerciseFallbacks();
    });
    vitest_1.it.each([undefined, 'false', 'FALSE', '0', 'no', 'off'])('preserves existing client use for non-disabling flag %s', async (flag) => {
        fakeEnv = { DISABLE_OPENAI: flag, OPENAI_API_KEY: 'offline-fake-key' };
        await (0, priceComparison_1.selectBestMatch)('Cabinet', [{}], 'homeDepot');
        await (0, priceComparison_1.generateProductMetadata)('Cabinet', 'cabinet');
        await (0, globalMaterials_1.selectBestGlobalMatch)('Cabinet', [candidate, Object.assign(Object.assign({}, candidate), { id: 'other' })]);
        await (0, globalMaterials_1.validateGlobalMatch)('Cabinet', candidate);
        (0, vitest_1.expect)(openai_1.default).toHaveBeenCalledTimes(4);
        (0, vitest_1.expect)(mocks.create).toHaveBeenCalledTimes(4);
        (0, vitest_1.expect)(globalThis.fetch).not.toHaveBeenCalled();
        (0, vitest_1.expect)(node_net_1.Socket.prototype.connect).not.toHaveBeenCalled();
    });
    (0, vitest_1.it)('preserves missing-key fallbacks without the disable flag', async () => {
        await exerciseFallbacks();
    });
    (0, vitest_1.it)('evaluates the flag at call time rather than caching it', () => {
        const env = { OPENAI_API_KEY: 'offline-fake-key', DISABLE_OPENAI: 'false' };
        (0, vitest_1.expect)((0, openaiAvailability_1.isOpenAIEnabled)(env)).toBe(true);
        env.DISABLE_OPENAI = 'true';
        (0, vitest_1.expect)((0, openaiAvailability_1.isOpenAIEnabled)(env)).toBe(false);
    });
    (0, vitest_1.it)('keeps empty-result paths independent of OpenAI', async () => {
        (0, vitest_1.expect)((await (0, priceComparison_1.selectBestMatch)('Cabinet', [], 'homeDepot')).index).toBe(-1);
        (0, vitest_1.expect)((await (0, globalMaterials_1.selectBestGlobalMatch)('Cabinet', [])).candidate).toBeNull();
        (0, vitest_1.expect)(openai_1.default).not.toHaveBeenCalled();
    });
});
//# sourceMappingURL=openaiAvailability.test.js.map
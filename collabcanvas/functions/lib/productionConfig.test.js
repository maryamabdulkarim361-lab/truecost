"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const productionConfig_1 = require("./productionConfig");
(0, vitest_1.beforeEach)(() => {
    for (const key of ['K_SERVICE', 'FUNCTIONS_EMULATOR', 'USE_FIREBASE_EMULATORS', 'DISABLE_OPENAI', 'FIRESTORE_EMULATOR_HOST', 'FIREBASE_AUTH_EMULATOR_HOST', 'FIREBASE_STORAGE_EMULATOR_HOST', 'STORAGE_EMULATOR_HOST', 'FIREBASE_DATABASE_EMULATOR_HOST'])
        vitest_1.vi.stubEnv(key, '');
    vitest_1.vi.stubEnv('APP_ENV', 'production');
    vitest_1.vi.stubEnv('GCLOUD_PROJECT', 'example-production');
    vitest_1.vi.stubEnv('PYTHON_FUNCTIONS_URL', 'https://us-central1-example-production.cloudfunctions.net');
    vitest_1.vi.stubGlobal('fetch', () => { throw new Error('Network forbidden'); });
});
(0, vitest_1.afterEach)(() => { vitest_1.vi.unstubAllEnvs(); vitest_1.vi.unstubAllGlobals(); });
(0, vitest_1.it)('accepts explicit production configuration', () => { (0, vitest_1.expect)((0, productionConfig_1.isProduction)()).toBe(true); (0, vitest_1.expect)((0, productionConfig_1.productionPythonUrl)()).toContain('example-production'); });
vitest_1.it.each(['FUNCTIONS_EMULATOR', 'USE_FIREBASE_EMULATORS', 'DISABLE_OPENAI', 'FIRESTORE_EMULATOR_HOST', 'FIREBASE_AUTH_EMULATOR_HOST'])('rejects %s', key => { vitest_1.vi.stubEnv(key, 'true'); (0, vitest_1.expect)(() => (0, productionConfig_1.productionPythonUrl)()).toThrow(); });
vitest_1.it.each(['', 'http://127.0.0.1:5003', 'https://us-central1-collabcanvas-dev.cloudfunctions.net', 'https://example.com/collabcanvas-dev/us-central1', 'https://fake:fake@example.com'])('rejects unsafe endpoint %s', url => { vitest_1.vi.stubEnv('PYTHON_FUNCTIONS_URL', url); (0, vitest_1.expect)(() => (0, productionConfig_1.productionPythonUrl)()).toThrow(); });
(0, vitest_1.it)('requires production project', () => { vitest_1.vi.stubEnv('GCLOUD_PROJECT', 'collabcanvas-dev'); (0, vitest_1.expect)(() => (0, productionConfig_1.productionPythonUrl)()).toThrow(); });
(0, vitest_1.it)('cannot override managed runtime with local mode', () => { vitest_1.vi.stubEnv('APP_ENV', 'development'); vitest_1.vi.stubEnv('K_SERVICE', 'fake'); (0, vitest_1.expect)((0, productionConfig_1.isProduction)()).toBe(true); });
//# sourceMappingURL=productionConfig.test.js.map
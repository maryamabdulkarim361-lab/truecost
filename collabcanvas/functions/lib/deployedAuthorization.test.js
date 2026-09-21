"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/** Exercise actual exported handlers; providers and network must remain untouched. */
const vitest_1 = require("vitest");
const node_net_1 = require("node:net");
vitest_1.vi.mock('firebase-functions/v2/https', () => ({ onRequest: (_o, h) => h, onCall: (_o, h) => h, HttpsError: class extends Error {
        constructor(c, m) { super(m); this.code = c; }
    } }));
vitest_1.vi.mock('firebase-functions/params', () => ({ defineSecret: () => ({ value: () => { throw new Error('Credential access forbidden'); } }) }));
vitest_1.vi.mock('firebase-admin/app', () => ({ getApps: () => [{}], initializeApp: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-admin', () => ({ apps: [{}], app: () => ({}), initializeApp: vitest_1.vi.fn() }));
vitest_1.vi.mock('firebase-admin/firestore', () => ({ getFirestore: () => ({ collection: () => ({ doc: () => ({ get: async () => ({ exists: true, data: () => ({ ownerId: 'owner', collaborators: [] }) }) }) }) }), FieldValue: { serverTimestamp: () => 0 } }));
vitest_1.vi.mock('openai', () => ({ default: class {
        constructor() { throw new Error('OpenAI construction forbidden'); }
    } }));
vitest_1.vi.mock('aws-sdk', () => ({ default: { SageMakerRuntime: class {
            constructor() { throw new Error('AWS construction forbidden'); }
        } } }));
const aiCommand_1 = require("./aiCommand");
const materialEstimateCommand_1 = require("./materialEstimateCommand");
const pricing_1 = require("./pricing");
const sagemakerInvoke_1 = require("./sagemakerInvoke");
const clarificationAgent_1 = require("./clarificationAgent");
const estimationPipeline_1 = require("./estimationPipeline");
const priceComparison_1 = require("./priceComparison");
const annotationCheckAgent_1 = require("./annotationCheckAgent");
const estimatePipelineOrchestrator_1 = require("./estimatePipelineOrchestrator");
const handlers = Object.entries({ aiCommand: aiCommand_1.aiCommand, materialEstimateCommand: materialEstimateCommand_1.materialEstimateCommand, getHomeDepotPrice: pricing_1.getHomeDepotPrice, sagemakerInvoke: sagemakerInvoke_1.sagemakerInvoke, clarificationAgent: clarificationAgent_1.clarificationAgent, estimationPipeline: estimationPipeline_1.estimationPipeline, comparePrices: priceComparison_1.comparePrices, annotationCheckAgent: annotationCheckAgent_1.annotationCheckAgent, triggerEstimatePipeline: estimatePipelineOrchestrator_1.triggerEstimatePipeline, updatePipelineStage: estimatePipelineOrchestrator_1.updatePipelineStage });
(0, vitest_1.beforeEach)(() => {
    vitest_1.vi.stubEnv('APP_ENV', 'development');
    vitest_1.vi.stubEnv('K_SERVICE', '');
    const blocked = () => { throw new Error('Network forbidden'); };
    vitest_1.vi.spyOn(node_net_1.Socket.prototype, 'connect').mockImplementation(blocked);
    vitest_1.vi.stubGlobal('fetch', blocked);
});
(0, vitest_1.afterEach)(() => { vitest_1.vi.restoreAllMocks(); vitest_1.vi.unstubAllGlobals(); vitest_1.vi.unstubAllEnvs(); });
vitest_1.it.each(handlers)('%s rejects missing identity before any provider work', async (_name, handler) => {
    await (0, vitest_1.expect)(handler({ data: { projectId: 'p' }, rawRequest: { headers: {} } })).rejects.toMatchObject({ code: 'unauthenticated' });
});
vitest_1.it.each(handlers)('%s rejects cross-user project before provider work', async (_name, handler) => {
    await (0, vitest_1.expect)(handler({ auth: { uid: 'attacker' }, data: { projectId: 'p' }, rawRequest: { headers: {} } })).rejects.toMatchObject({ code: 'permission-denied' });
});
//# sourceMappingURL=deployedAuthorization.test.js.map
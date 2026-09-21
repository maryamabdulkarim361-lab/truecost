"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/** Isolated demo project; only emulator RPCs. No Functions/provider invocation. */
const node_fs_1 = require("node:fs");
const node_path_1 = require("node:path");
const node_crypto_1 = require("node:crypto");
const vitest_1 = require("vitest");
const rules_unit_testing_1 = require("@firebase/rules-unit-testing");
const firestore_1 = require("firebase/firestore");
let env;
const paths = ['durableJobs/job', 'durableJobs/job/checkpoints/op', 'durableJobs/job/leases/lease', 'durableJobs/job/dispatchIntents/op'];
(0, vitest_1.beforeAll)(async () => {
    if (process.env.FIRESTORE_EMULATOR_HOST !== '127.0.0.1:8081')
        throw new Error('Exact local emulator required');
    env = await (0, rules_unit_testing_1.initializeTestEnvironment)({ projectId: 'demo-durable-rules-' + (0, node_crypto_1.randomUUID)().slice(0, 8), firestore: {
            host: '127.0.0.1', port: 8081, rules: (0, node_fs_1.readFileSync)((0, node_path_1.resolve)(__dirname, '../../firestore.rules'), 'utf8')
        } });
    await env.withSecurityRulesDisabled(async (context) => {
        const db = context.firestore();
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'projects/project'), { ownerId: 'owner', collaborators: [{ userId: 'editor', role: 'editor' }, { userId: 'viewer', role: 'viewer' }] });
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/estimate'), { userId: 'owner', durableJobId: 'job', status: 'processing' });
        for (const path of paths)
            await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, path), { ownerUid: 'owner', projectId: 'project', generation: 1, status: 'running' });
    });
}, 30000);
(0, vitest_1.afterAll)(async () => { if (env) {
    try {
        await env.clearFirestore();
    }
    finally {
        await env.cleanup();
    }
} });
for (const uid of ['owner', 'editor', 'viewer', 'attacker', null]) {
    vitest_1.it.each(paths)(`blocks ${uid !== null && uid !== void 0 ? uid : 'anonymous'} durable writes at %s`, async (path) => {
        const db = (uid ? env.authenticatedContext(uid) : env.unauthenticatedContext()).firestore();
        await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, path), { generation: 999, currentOperation: 'final', leaseOwner: 'forged', dispatchIntent: { status: 'pending' } }));
        await (0, rules_unit_testing_1.assertFails)((0, firestore_1.setDoc)((0, firestore_1.doc)(db, path + '-forged'), { ownerUid: uid, status: 'final' }));
        await (0, rules_unit_testing_1.assertFails)((0, firestore_1.deleteDoc)((0, firestore_1.doc)(db, path)));
        await (0, rules_unit_testing_1.assertFails)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, path)));
    });
}
(0, vitest_1.it)('owner still reads estimate projection; Admin writes are privileged', async () => {
    const db = env.authenticatedContext('owner').firestore();
    await (0, rules_unit_testing_1.assertSucceeds)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, 'estimates/estimate')));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'estimates/estimate'), { totalCost: 1 }));
    await env.withSecurityRulesDisabled(async (context) => {
        var _a;
        await (0, firestore_1.updateDoc)((0, firestore_1.doc)(context.firestore(), 'durableJobs/job'), { generation: 2 });
        (0, vitest_1.expect)((_a = (await (0, firestore_1.getDoc)((0, firestore_1.doc)(context.firestore(), 'durableJobs/job'))).data()) === null || _a === void 0 ? void 0 : _a.generation).toBe(2);
    });
});
//# sourceMappingURL=durable.rules.test.js.map
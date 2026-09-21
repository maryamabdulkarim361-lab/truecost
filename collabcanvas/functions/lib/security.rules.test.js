"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/** Only localhost emulator traffic, isolated demo project; never production data. */
const node_fs_1 = require("node:fs");
const node_path_1 = require("node:path");
const vitest_1 = require("vitest");
const rules_unit_testing_1 = require("@firebase/rules-unit-testing");
const firestore_1 = require("firebase/firestore");
let env;
(0, vitest_1.beforeAll)(async () => {
    const host = process.env.FIRESTORE_EMULATOR_HOST || '127.0.0.1:8081';
    if (!['127.0.0.1:8081', '127.0.0.1:18181'].includes(host))
        throw new Error('Local test emulator required');
    env = await (0, rules_unit_testing_1.initializeTestEnvironment)({ projectId: 'demo-security-hardening-v1', firestore: { host: '127.0.0.1', port: Number(host.split(':')[1]), rules: (0, node_fs_1.readFileSync)((0, node_path_1.resolve)(__dirname, '../../firestore.rules'), 'utf8') } });
    await env.withSecurityRulesDisabled(async (context) => {
        const db = context.firestore();
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'projects/p'), { ownerId: 'owner', collaborators: [{ userId: 'editor', role: 'editor' }, { userId: 'viewer', role: 'viewer' }], createdBy: 'owner', createdAt: 0 });
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'projects/p/shapes/s'), { createdBy: 'owner' });
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'projects/p/pipeline/status'), { status: 'complete' });
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/e'), { userId: 'owner', status: 'processing' });
        await (0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/e/agentOutputs/location'), { userId: 'attacker', output: {} });
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
vitest_1.it.each(['owner', 'editor', 'viewer'])('permits project member %s', async (uid) => {
    const db = env.authenticatedContext(uid).firestore();
    await (0, rules_unit_testing_1.assertSucceeds)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, 'projects/p')));
    await (0, rules_unit_testing_1.assertSucceeds)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, 'projects/p/pipeline/status')));
});
(0, vitest_1.it)('blocks unrelated project access despite a collaborators list', async () => {
    const db = env.authenticatedContext('attacker').firestore();
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, 'projects/p')));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.getDoc)((0, firestore_1.doc)(db, 'projects/p/pipeline/status')));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'projects/p'), { name: 'stolen' }));
});
(0, vitest_1.it)('prevents editors changing collaborator permissions', async () => {
    const db = env.authenticatedContext('editor').firestore();
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'projects/p'), { collaborators: [{ userId: 'attacker', role: 'editor' }] }));
});
(0, vitest_1.it)('enforces estimate parent owner even when child contains another userId', async () => {
    const owner = env.authenticatedContext('owner').firestore();
    const attacker = env.authenticatedContext('attacker').firestore();
    await (0, rules_unit_testing_1.assertSucceeds)((0, firestore_1.getDoc)((0, firestore_1.doc)(owner, 'estimates/e/agentOutputs/location')));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.getDoc)((0, firestore_1.doc)(attacker, 'estimates/e')));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.getDoc)((0, firestore_1.doc)(attacker, 'estimates/e/agentOutputs/location')));
});
(0, vitest_1.it)('viewer cannot edit project or pipeline', async () => {
    const db = env.authenticatedContext('viewer').firestore();
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'projects/p'), { name: 'changed' }));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'projects/p/pipeline/status'), { status: 'fake' }));
});
(0, vitest_1.it)('blocks cross-user and viewer project-shape deletion; editor succeeds', async () => {
    for (const uid of ['attacker', 'viewer']) {
        await (0, rules_unit_testing_1.assertFails)((0, firestore_1.deleteDoc)((0, firestore_1.doc)(env.authenticatedContext(uid).firestore(), 'projects/p/shapes/s')));
    }
    await (0, rules_unit_testing_1.assertSucceeds)((0, firestore_1.deleteDoc)((0, firestore_1.doc)(env.authenticatedContext('editor').firestore(), 'projects/p/shapes/s')));
});
(0, vitest_1.it)('denies owner forgery of pipeline root, output and monetary ledger', async () => {
    const db = env.authenticatedContext('owner').firestore();
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.updateDoc)((0, firestore_1.doc)(db, 'estimates/e'), { totalCost: 1, status: 'completed' }));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/forged'), { userId: 'owner', status: 'completed' }));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/e/agentOutputs/cost'), { output: { totalCost: 1 } }));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.setDoc)((0, firestore_1.doc)(db, 'estimates/e/costItems/forged'), { total: 1 }));
    await (0, rules_unit_testing_1.assertFails)((0, firestore_1.deleteDoc)((0, firestore_1.doc)(db, 'estimates/e')));
});
//# sourceMappingURL=security.rules.test.js.map
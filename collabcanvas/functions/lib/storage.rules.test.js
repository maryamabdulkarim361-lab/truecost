"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
/** Isolated localhost emulators only. No production bucket or credentials. */
const node_fs_1 = require("node:fs");
const node_path_1 = require("node:path");
const vitest_1 = require("vitest");
const rules_unit_testing_1 = require("@firebase/rules-unit-testing");
const firestore_1 = require("firebase/firestore");
const storage_1 = require("firebase/storage");
let env;
async function setupStep(name, operation) {
    let timer;
    try {
        return await Promise.race([operation, new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(`Storage setup stalled: ${name}`)), 8000);
            })]);
    }
    finally {
        clearTimeout(timer);
    }
}
const bytes = new Uint8Array([1, 2, 3]);
(0, vitest_1.beforeAll)(async () => {
    env = await setupStep('initialize rules', (0, rules_unit_testing_1.initializeTestEnvironment)({ projectId: 'demo-storage-remediation-v1',
        firestore: { host: '127.0.0.1', port: Number(process.env.STORAGE_TEST_FIRESTORE_PORT || 18181), rules: 'rules_version = "2"; service cloud.firestore { match /databases/{database}/documents { match /{document=**} { allow read, write: if false; } } }' },
        storage: { host: '127.0.0.1', port: Number(process.env.STORAGE_TEST_STORAGE_PORT || 19199), rules: (0, node_fs_1.readFileSync)((0, node_path_1.resolve)(__dirname, '../../storage.rules'), 'utf8') } }));
    await env.withSecurityRulesDisabled(async (ctx) => {
        await setupStep('seed Firestore project', (0, firestore_1.setDoc)((0, firestore_1.doc)(ctx.firestore(), 'projects/p'), { ownerId: 'owner', collaborators: [{ userId: 'editor', role: 'editor' }, { userId: 'viewer', role: 'viewer' }] }));
        for (const path of ['construction-plans/owner/plan', 'projects/p/plans/plan', 'pdfs/e/estimate.pdf']) {
            const storage = ctx.storage();
            storage.setMaxOperationRetryTime(0);
            await setupStep('seed Storage object', (0, storage_1.uploadBytes)((0, storage_1.ref)(storage, path), bytes));
        }
    });
}, 30000);
(0, vitest_1.afterAll)(async () => { if (env)
    await env.cleanup(); });
vitest_1.it.each(['attacker', null])('denies cross-user/anonymous blueprint reads %s', async (uid) => {
    const ctx = uid ? env.authenticatedContext(uid) : env.unauthenticatedContext();
    for (const path of ['construction-plans/owner/plan', 'projects/p/plans/plan'])
        await (0, rules_unit_testing_1.assertFails)((0, storage_1.getBytes)((0, storage_1.ref)(ctx.storage(), path)));
});
vitest_1.it.each(['attacker', 'viewer'])('denies blueprint overwrite, upload and deletion %s', async (uid) => {
    const storage = env.authenticatedContext(uid).storage();
    for (const path of ['construction-plans/owner/plan', 'projects/p/plans/plan']) {
        await (0, rules_unit_testing_1.assertFails)((0, storage_1.uploadBytes)((0, storage_1.ref)(storage, path), bytes));
        await (0, rules_unit_testing_1.assertFails)((0, storage_1.deleteObject)((0, storage_1.ref)(storage, path)));
    }
    await (0, rules_unit_testing_1.assertFails)((0, storage_1.uploadBytes)((0, storage_1.ref)(storage, 'projects/p/plans/new'), bytes));
});
vitest_1.it.each(['owner', 'editor'])('allows legitimate project read/write/delete %s', async (uid) => {
    const storage = env.authenticatedContext(uid).storage();
    await (0, rules_unit_testing_1.assertSucceeds)((0, storage_1.getBytes)((0, storage_1.ref)(storage, 'projects/p/plans/plan')));
    const target = (0, storage_1.ref)(storage, `projects/p/plans/${uid}`);
    await (0, rules_unit_testing_1.assertSucceeds)((0, storage_1.uploadBytes)(target, bytes));
    await (0, rules_unit_testing_1.assertSucceeds)((0, storage_1.deleteObject)(target));
});
(0, vitest_1.it)('allows viewer read only', async () => {
    await (0, rules_unit_testing_1.assertSucceeds)((0, storage_1.getBytes)((0, storage_1.ref)(env.authenticatedContext('viewer').storage(), 'projects/p/plans/plan')));
});
(0, vitest_1.it)('allows personal owner and denies unknown canvas ownership', async () => {
    const storage = env.authenticatedContext('owner').storage();
    await (0, rules_unit_testing_1.assertSucceeds)((0, storage_1.getBytes)((0, storage_1.ref)(storage, 'construction-plans/owner/plan')));
    await (0, rules_unit_testing_1.assertFails)((0, storage_1.uploadBytes)((0, storage_1.ref)(storage, 'construction-plans/arbitrary-canvas/plan'), bytes));
});
vitest_1.it.each(['owner', 'attacker', null])('denies direct report read/overwrite/delete %s', async (uid) => {
    const storage = (uid ? env.authenticatedContext(uid) : env.unauthenticatedContext()).storage();
    const report = (0, storage_1.ref)(storage, 'pdfs/e/estimate.pdf');
    await (0, rules_unit_testing_1.assertFails)((0, storage_1.getBytes)(report));
    await (0, rules_unit_testing_1.assertFails)((0, storage_1.uploadBytes)(report, bytes));
    await (0, rules_unit_testing_1.assertFails)((0, storage_1.deleteObject)(report));
});
//# sourceMappingURL=storage.rules.test.js.map
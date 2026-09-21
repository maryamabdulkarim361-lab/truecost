"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
const vitest_1 = require("vitest");
const node_child_process_1 = require("node:child_process");
const node_path_1 = require("node:path");
vitest_1.vi.mock('./functionSecurity', () => ({ onCall: () => () => { throw new Error('Agent invocation forbidden'); }, allowedOrigins: () => [] }));
vitest_1.vi.mock('openai', () => ({ OpenAI: class {
        constructor() { throw new Error('Provider forbidden'); }
    } }));
const node_fs_1 = require("node:fs");
const ts = require("typescript");
// Execute the actual pure legacy builder without importing Firebase/browser code.
const legacySource = (0, node_fs_1.readFileSync)((0, node_path_1.resolve)(__dirname, '../../src/services/pipelineService.ts'), 'utf8');
const legacyFunction = legacySource.slice(legacySource.indexOf('export function buildFallbackClarificationOutput')).replace('export ', '');
const buildFallbackClarificationOutput = new Function(ts.transpileModule(legacyFunction, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None } }).outputText + ';return buildFallbackClarificationOutput;')();
const estimationPipeline_1 = require("./estimationPipeline");
const annotationQuantifier_1 = require("./annotationQuantifier");
const enhancedCsiMapper_1 = require("./enhancedCsiMapper");
const projectSpecificExtractor_1 = require("./projectSpecificExtractor");
function produce(minimum = false, incomplete = false) {
    const clarificationData = { projectType: minimum ? 'other' : 'kitchen_remodel', finishLevel: 'mid_range',
        location: { fullAddress: '100 Test St, Denver CO 80202', streetAddress: '100 Test St', city: 'Denver', state: 'CO', zipCode: incomplete ? '' : '80202' },
        flexibility: 'flexible' };
    const scopeText = minimum ? 'Paint the measured room' : 'Kitchen remodel with cabinets and countertops';
    const quantities = (0, annotationQuantifier_1.computeQuantitiesFromAnnotations)({ scale: { pixelsPerUnit: 10, unit: 'feet' },
        layers: [{ id: 'room', name: minimum ? 'Room' : 'Kitchen', visible: true, shapeCount: 1 }],
        shapes: [{ id: 'r1', type: 'polygon', label: minimum ? 'Room' : 'Kitchen', layerId: 'room', x: 0, y: 0, w: 140, h: 140, points: [0, 0, 140, 0, 140, 140, 0, 140], source: 'manual' }] });
    const specific = (0, projectSpecificExtractor_1.extractProjectSpecificData)(quantities, clarificationData.projectType, clarificationData, scopeText);
    return (0, estimationPipeline_1.assembleClarificationOutput)({ estimateId: minimum ? 'minimum' : 'normal', clarificationData, scopeText,
        quantities, csiScope: (0, estimationPipeline_1.createCSIScope)((0, enhancedCsiMapper_1.buildEnhancedCSIItems)(quantities, { projectType: clarificationData.projectType, finishLevel: 'mid_range', scopeText, clarificationData, clarificationContext: {} })),
        planImageUrl: 'https://example.invalid/plan.png', spaceModel: (0, annotationQuantifier_1.buildSpaceModelFromQuantities)(quantities), projectSpecificData: specific,
        spatialNarrative: (0, projectSpecificExtractor_1.generateLayoutNarrative)(quantities, clarificationData.projectType, specific), inferenceResult: null }).fixedOutput;
}
function consume(outputs) {
    const root = (0, node_path_1.resolve)(__dirname, '../../..');
    const result = (0, node_child_process_1.spawnSync)((0, node_path_1.resolve)(root, 'functions/venv/bin/python'), ['-B', (0, node_path_1.resolve)(root, 'functions/tests/contracts/clarification_bridge.py')], {
        input: JSON.stringify(outputs), encoding: 'utf8', env: { PATH: process.env.PATH, PYTHONPATH: (0, node_path_1.resolve)(root, 'functions') }, timeout: 30000
    });
    (0, vitest_1.expect)(result.status, result.stderr).toBe(0);
    return JSON.parse(result.stdout.trim().split('\n').at(-1));
}
const request = (output, key = output.estimateId) => ({ projectId: 'project', userId: 'owner', idempotencyKey: key, clarificationOutput: output });
(0, vitest_1.it)('actual production assembler output crosses strict Python start without agents', { timeout: 30000 }, () => {
    const normal = produce();
    const minimum = produce(true);
    const result = consume([request(normal), request(minimum)]);
    (0, vitest_1.expect)(result.map((r) => r.status), JSON.stringify(result)).toEqual([202, 202]);
});
(0, vitest_1.it)('missing, malformed, legacy inputs fail closed; duplicates preserve identity', { timeout: 30000 }, () => {
    const normal = produce();
    const missing = structuredClone(normal);
    delete missing.cadData;
    const malformed = structuredClone(normal);
    malformed.projectBrief.projectType = 'invalid';
    const changed = structuredClone(normal);
    changed.projectBrief.scopeSummary.description = 'Changed user scope';
    const result = consume([request(normal), request(normal), request(changed), request(missing, 'missing'), request(malformed, 'malformed'), request({ estimateId: 'legacy', projectBrief: 'legacy' })]);
    (0, vitest_1.expect)(result.map((r) => r.status)).toEqual([202, 202, 409, 400, 400, 400]);
    (0, vitest_1.expect)(result[0].data).toEqual(result[1].data);
});
(0, vitest_1.it)('verified UID controls project authorization', { timeout: 30000 }, () => {
    const output = produce();
    const r = request(output);
    (0, vitest_1.expect)(consume([Object.assign(Object.assign({}, r), { userId: 'attacker' }), Object.assign(Object.assign({}, r), { projectId: 'other' })]).map((v) => v.status)).toEqual([403, 404]);
});
(0, vitest_1.it)('producer review state and identity contradictions cannot start a durable job', { timeout: 30000 }, () => {
    const normal = produce();
    const review = structuredClone(normal);
    review.clarificationStatus = 'needs_review';
    const flagged = structuredClone(normal);
    flagged.flags.userVerificationRequired = true;
    const typed = structuredClone(normal);
    typed.projectBrief.scopeSummary.totalSqft = '196';
    const project = structuredClone(normal);
    project.projectId = 'other';
    const owner = structuredClone(normal);
    owner.userId = 'attacker';
    const responses = consume([request(review, 'review'), request(flagged, 'flagged'), request(typed, 'typed'), request(project, 'project'), request(owner, 'owner'), Object.assign(Object.assign({}, request(normal)), { estimateId: 'wrong' })]);
    (0, vitest_1.expect)(responses.map((r) => r.status)).toEqual([400, 400, 400, 400, 403, 400]);
});
(0, vitest_1.it)('incomplete actual producer stays review-required without fabricated location', { timeout: 30000 }, () => {
    const output = produce(false, true);
    (0, vitest_1.expect)(output.clarificationStatus).toBe('needs_review');
    (0, vitest_1.expect)(output.projectBrief.location.zipCode).toBe('');
    (0, vitest_1.expect)(consume([request(output)])[0].status).toBe(400);
});
(0, vitest_1.it)('actual reachable debug fallback is rejected by strict start', { timeout: 30000 }, () => {
    const legacy = buildFallbackClarificationOutput({ projectId: 'project', estimateId: 'legacy-debug' });
    (0, vitest_1.expect)(consume([request(legacy)])[0].status).toBe(400);
});
//# sourceMappingURL=clarificationContract.test.js.map
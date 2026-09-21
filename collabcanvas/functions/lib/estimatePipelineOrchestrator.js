"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.updatePipelineStage = exports.triggerEstimatePipeline = exports.getPythonPipelineUrl = void 0;
const node_crypto_1 = require("node:crypto");
const safeDiagnostics_1 = require("./safeDiagnostics");
const productionConfig_1 = require("./productionConfig");
/**
 * Estimate Pipeline Orchestrator
 * Dedicated cloud function to trigger and initialize the estimate generation pipeline.
 * Story: 6-2 - Two-phase UI with progress tracking
 */
const https_1 = require("firebase-functions/v2/https");
const functionSecurity_1 = require("./functionSecurity");
const firestore_1 = require("firebase-admin/firestore");
const app_1 = require("firebase-admin/app");
// Lazy initialization to avoid timeout during module load
let _db = null;
function getDb() {
    if (!_db) {
        // Initialize Firebase Admin if not already initialized
        if ((0, app_1.getApps)().length === 0) {
            (0, app_1.initializeApp)();
        }
        _db = (0, firestore_1.getFirestore)();
    }
    return _db;
}
/**
 * Pipeline stages for the estimate generation pipeline
 * Note: Clarification runs separately during Annotate phase (Epic 3)
 */
const _PIPELINE_STAGES = [
    'cad_analysis',
    'location',
    'scope',
    'code_compliance',
    'cost',
    'risk',
    'timeline',
    'final',
];
/**
 * Get the Python pipeline URL based on environment
 */
function getPythonPipelineUrl() {
    if ((0, productionConfig_1.isProduction)())
        return `${(0, productionConfig_1.productionPythonUrl)()}/start_deep_pipeline`;
    // Allow override via environment variable for flexible local dev
    if (process.env.PYTHON_FUNCTIONS_URL) {
        const base = process.env.PYTHON_FUNCTIONS_URL.replace(/\/+$/, '').replace(/\/start_deep_pipeline$/, '');
        const url = new URL(base);
        const prefix = url.pathname === '/' && ['localhost', '127.0.0.1'].includes(url.hostname)
            ? `${base}/collabcanvas-dev/us-central1` : base;
        return `${prefix}/start_deep_pipeline`;
    }
    // Check if we're running in the emulator
    if (process.env.FUNCTIONS_EMULATOR === 'true') {
        // Python functions run on separate port (5003) to avoid conflict with TS emulator (5001)
        // Start Python server: cd ../functions && source venv/bin/activate && python serve_local.py
        return 'http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline';
    }
    // Production URL
    return 'https://us-central1-collabcanvas-dev.cloudfunctions.net/start_deep_pipeline';
}
exports.getPythonPipelineUrl = getPythonPipelineUrl;
/**
 * Gather project context data for the pipeline
 */
async function gatherProjectContext(projectId) {
    const db = getDb();
    // Get project document
    const projectRef = db.collection('projects').doc(projectId);
    const projectDoc = await projectRef.get();
    const projectData = projectDoc.data();
    // Get board state (background image)
    const boardRef = db.collection('projects').doc(projectId).collection('board').doc('state');
    const boardDoc = await boardRef.get();
    const boardData = boardDoc.exists ? boardDoc.data() : null;
    // Get scope items
    const scopeRef = db.collection('projects').doc(projectId).collection('scope').doc('data');
    const scopeDoc = await scopeRef.get();
    const scopeData = scopeDoc.exists ? scopeDoc.data() : null;
    // Get shapes (annotations)
    const shapesRef = db.collection('projects').doc(projectId).collection('shapes');
    const shapesSnapshot = await shapesRef.get();
    const shapes = [];
    shapesSnapshot.forEach((doc) => {
        const data = doc.data();
        shapes.push(Object.assign({ id: doc.id, type: data.type, x: data.x, y: data.y, w: data.w, h: data.h }, data));
    });
    return {
        projectId,
        projectName: (projectData === null || projectData === void 0 ? void 0 : projectData.name) || '',
        projectDescription: (projectData === null || projectData === void 0 ? void 0 : projectData.description) || '',
        backgroundImage: (boardData === null || boardData === void 0 ? void 0 : boardData.backgroundImage)
            ? {
                url: boardData.backgroundImage.url,
                width: boardData.backgroundImage.width,
                height: boardData.backgroundImage.height,
            }
            : null,
        scopeItems: (scopeData === null || scopeData === void 0 ? void 0 : scopeData.items) || [],
        shapes,
    };
}
/**
 * Trigger the estimate generation pipeline
 * This function:
 * 1. Validates the request
 * 2. Gathers project context (scope, plan, annotations)
 * 3. Creates/updates the pipeline status document with context
 * 4. Returns a pipelineId for tracking
 *
 * The actual agent execution is handled by the existing deep pipeline
 * infrastructure from Epic 2.
 */
exports.triggerEstimatePipeline = (0, functionSecurity_1.onCall)({
    cors: (0, functionSecurity_1.allowedOrigins)(),
    maxInstances: 10,
    memory: '512MiB', // Increased for context gathering
}, async (request) => {
    var _a, _b;
    try {
        const { projectId, clarificationOutput: suppliedClarification } = request.data;
        // Validate required fields
        if (!projectId) {
            throw new https_1.HttpsError('invalid-argument', 'Project ID is required');
        }
        // =================== SECURITY: Authentication Check ===================
        // Verify auth first and bind userId from the authenticated identity
        // IMPORTANT: Never trust request.data.userId - always use request.auth.uid
        if (!request.auth) {
            throw new https_1.HttpsError('unauthenticated', 'User must be authenticated');
        }
        // Bind userId from authenticated identity - ignore any caller-supplied userId
        const userId = request.auth.uid;
        if ((0, productionConfig_1.isProduction)()) {
            if (!suppliedClarification || typeof suppliedClarification !== 'object') {
                throw new https_1.HttpsError('invalid-argument', 'Completed clarification required');
            }
            const canonical = (value) => Array.isArray(value) ? value.map(canonical) :
                value && typeof value === 'object' ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => [k, canonical(v)])) : value;
            const key = request.data.idempotencyKey || suppliedClarification.estimateId ||
                'est-' + (0, node_crypto_1.createHash)('sha256').update(JSON.stringify(canonical({ projectId, clarification: suppliedClarification }))).digest('hex');
            const authorization = request.rawRequest.headers.authorization;
            if (!authorization || !/^Bearer \S+$/i.test(authorization))
                throw new https_1.HttpsError('unauthenticated', 'Caller ID token required');
            const response = await fetch(getPythonPipelineUrl(), { method: 'POST', redirect: 'error',
                signal: AbortSignal.timeout(15000), headers: { 'Content-Type': 'application/json', Authorization: authorization },
                body: JSON.stringify({ userId, projectId, idempotencyKey: key, clarificationOutput: suppliedClarification }) });
            const result = await response.json();
            if (response.status !== 202 || !result.success || !((_a = result.data) === null || _a === void 0 ? void 0 : _a.jobId))
                throw new https_1.HttpsError('unavailable', 'Durable start unavailable');
            return Object.assign(Object.assign({}, result.data), { success: true, pipelineId: result.data.estimateId });
        }
        const db = getDb();
        // Verify the user has access to this project
        const projectRef = db.collection('projects').doc(projectId);
        const projectDoc = await projectRef.get();
        if (!projectDoc.exists) {
            throw new https_1.HttpsError('not-found', 'Project not found');
        }
        const projectData = projectDoc.data();
        if ((projectData === null || projectData === void 0 ? void 0 : projectData.ownerId) !== request.auth.uid) {
            // Check if user is a collaborator
            const collaborators = (projectData === null || projectData === void 0 ? void 0 : projectData.collaborators) || [];
            const isCollaborator = collaborators.some((c) => { var _a; return c.userId === ((_a = request.auth) === null || _a === void 0 ? void 0 : _a.uid) && c.role === 'editor'; });
            if (!isCollaborator) {
                throw new https_1.HttpsError('permission-denied', 'User does not have access to this project');
            }
        }
        // Gather project context data
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', `[PIPELINE] Gathering context for project ${projectId}`);
        const projectContext = await gatherProjectContext(projectId);
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', `[PIPELINE] Context gathered:`, {
            projectName: projectContext.projectName,
            hasBackgroundImage: !!projectContext.backgroundImage,
            scopeItemCount: projectContext.scopeItems.length,
            shapeCount: projectContext.shapes.length,
        });
        // Generate pipeline ID
        const pipelineId = `pipeline_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const startedAt = Date.now();
        if (!suppliedClarification || typeof suppliedClarification !== 'object' ||
            !suppliedClarification.projectBrief || typeof suppliedClarification.projectBrief !== 'object') {
            throw new https_1.HttpsError('invalid-argument', 'A completed clarificationOutput is required');
        }
        const clarificationOutput = Object.assign(Object.assign({}, suppliedClarification), { estimateId: pipelineId });
        // Initialize pipeline status document with context
        // Start from cad_analysis (clarification runs separately in Annotate phase)
        const pipelineStatus = {
            status: 'running',
            currentStage: 'cad_analysis',
            completedStages: [],
            startedAt,
            completedAt: null,
            triggeredBy: userId,
            projectId,
        };
        // Create/update the pipeline status document
        const statusRef = db.collection('projects').doc(projectId).collection('pipeline').doc('status');
        await statusRef.set(Object.assign(Object.assign({}, pipelineStatus), { pipelineId, updatedAt: firestore_1.FieldValue.serverTimestamp() }));
        // Store the project context separately for agent consumption
        const contextRef = db.collection('projects').doc(projectId).collection('pipeline').doc('context');
        await contextRef.set(Object.assign(Object.assign({}, projectContext), { pipelineId, createdAt: firestore_1.FieldValue.serverTimestamp() }));
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', `[PIPELINE] Started pipeline ${pipelineId} for project ${projectId}`);
        // Trigger the Python deep agent pipeline
        // The Python pipeline will sync progress back to /projects/{projectId}/pipeline/status
        try {
            const pythonPipelineUrl = getPythonPipelineUrl();
            (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', `[PIPELINE] Calling Python pipeline at: ${pythonPipelineUrl}`);
            // Forward completed clarification; never fabricate agent inputs.
            // Firebase onCall already verified this caller; Python independently verifies the same token.
            const authorization = request.rawRequest.headers.authorization;
            if (!authorization || !/^Bearer \S+$/i.test(authorization)) {
                throw new https_1.HttpsError('unauthenticated', 'Caller ID token required');
            }
            const response = await fetch(pythonPipelineUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: authorization,
                },
                body: JSON.stringify({
                    userId,
                    projectId,
                    clarificationOutput,
                }),
            });
            const pythonResult = await response.json();
            if (!response.ok || !pythonResult.success) {
                (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.error', '[PIPELINE] Python pipeline failed', { status: response.status });
                // Update status to error but don't throw - let the user see partial progress
                await statusRef.update({
                    status: 'error',
                    error: (0, safeDiagnostics_1.safeErrorMessage)(((_b = pythonResult.error) === null || _b === void 0 ? void 0 : _b.message) || 'Python pipeline failed to start'),
                    updatedAt: firestore_1.FieldValue.serverTimestamp(),
                });
                throw new https_1.HttpsError('internal', 'Python pipeline failed to start');
            }
            else {
                (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', '[PIPELINE] Python pipeline started:', pythonResult);
            }
        }
        catch (pythonError) {
            (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.error', '[PIPELINE] Failed to call Python pipeline');
            await statusRef.update({ status: 'error', error: 'Python pipeline failed to start', updatedAt: firestore_1.FieldValue.serverTimestamp() });
            throw new https_1.HttpsError('internal', 'Python pipeline failed to start');
        }
        return {
            success: true,
            pipelineId,
            message: 'Pipeline started successfully',
            status: pipelineStatus,
            context: {
                projectName: projectContext.projectName,
                hasBackgroundImage: !!projectContext.backgroundImage,
                scopeItemCount: projectContext.scopeItems.length,
                shapeCount: projectContext.shapes.length,
            },
        };
    }
    catch (error) {
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.error', '[PIPELINE] Error starting pipeline');
        if (error instanceof https_1.HttpsError) {
            throw error;
        }
        throw new https_1.HttpsError('internal', 'Operation failed');
    }
});
/**
 * Update pipeline stage (called by agent functions as they complete)
 */
// Valid pipeline stage values for validation
const VALID_PIPELINE_STAGES = _PIPELINE_STAGES;
/**
 * Validate that a stage value is a valid pipeline stage
 */
function isValidPipelineStage(stage) {
    return typeof stage === 'string' && VALID_PIPELINE_STAGES.includes(stage);
}
exports.updatePipelineStage = (0, functionSecurity_1.onCall)({
    cors: (0, functionSecurity_1.allowedOrigins)(),
    maxInstances: 20,
    memory: '256MiB',
}, async (request) => {
    try {
        const { projectId, completedStage, nextStage, error } = request.data;
        if ((0, productionConfig_1.isProduction)())
            throw new https_1.HttpsError('failed-precondition', 'Durable worker owns production progress');
        if (!projectId) {
            throw new https_1.HttpsError('invalid-argument', 'Project ID is required');
        }
        // =================== SECURITY: Authentication Check ===================
        if (!request.auth) {
            throw new https_1.HttpsError('unauthenticated', 'User must be authenticated');
        }
        // =================== SECURITY: Enum Validation ===================
        // Validate completedStage if provided
        if (completedStage !== undefined && completedStage !== null && !isValidPipelineStage(completedStage)) {
            throw new https_1.HttpsError('invalid-argument', 'Operation failed');
        }
        // Validate nextStage if provided
        if (nextStage !== undefined && nextStage !== null && !isValidPipelineStage(nextStage)) {
            throw new https_1.HttpsError('invalid-argument', 'Operation failed');
        }
        const db = getDb();
        // =================== SECURITY: Authorization Check ===================
        // Verify the user has access to this project (owner or collaborator)
        const projectRef = db.collection('projects').doc(projectId);
        const projectDoc = await projectRef.get();
        if (!projectDoc.exists) {
            throw new https_1.HttpsError('not-found', 'Project not found');
        }
        const projectData = projectDoc.data();
        if ((projectData === null || projectData === void 0 ? void 0 : projectData.ownerId) !== request.auth.uid) {
            // Check if user is a collaborator
            const collaborators = (projectData === null || projectData === void 0 ? void 0 : projectData.collaborators) || [];
            const isCollaborator = collaborators.some((c) => { var _a; return c.userId === ((_a = request.auth) === null || _a === void 0 ? void 0 : _a.uid) && c.role === 'editor'; });
            if (!isCollaborator) {
                throw new https_1.HttpsError('permission-denied', 'User does not have access to this project');
            }
        }
        const statusRef = db.collection('projects').doc(projectId).collection('pipeline').doc('status');
        const statusDoc = await statusRef.get();
        if (!statusDoc.exists) {
            throw new https_1.HttpsError('not-found', 'Pipeline status not found');
        }
        const currentStatus = statusDoc.data();
        const completedStages = [...(currentStatus.completedStages || [])];
        if (completedStage && !completedStages.includes(completedStage)) {
            completedStages.push(completedStage);
        }
        const updateData = {
            completedStages,
            updatedAt: firestore_1.FieldValue.serverTimestamp(),
        };
        if (error) {
            updateData.status = 'error';
            updateData.error = error;
            updateData.completedAt = Date.now();
        }
        else if (nextStage) {
            updateData.currentStage = nextStage;
        }
        else if (completedStage === 'final') {
            // Pipeline complete
            updateData.status = 'complete';
            updateData.currentStage = null;
            updateData.completedAt = Date.now();
        }
        await statusRef.update(updateData);
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.log', `[PIPELINE] Updated stage for project ${projectId}: ${completedStage} -> ${nextStage || 'complete'}`);
        return {
            success: true,
            completedStages,
            currentStage: updateData.currentStage || currentStatus.currentStage,
            status: updateData.status || currentStatus.status,
        };
    }
    catch (error) {
        (0, safeDiagnostics_1.safeLog)('estimatePipelineOrchestrator.error', '[PIPELINE] Error updating stage:', error);
        if (error instanceof https_1.HttpsError) {
            throw error;
        }
        throw new https_1.HttpsError('internal', 'Operation failed');
    }
});
//# sourceMappingURL=estimatePipelineOrchestrator.js.map
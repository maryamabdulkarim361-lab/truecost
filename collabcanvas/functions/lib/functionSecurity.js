"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.onCall = exports.authorizeRequest = exports.authorizeProject = exports.checkOrigin = exports.allowedOrigins = void 0;
const https_1 = require("firebase-functions/v2/https");
const firestore_1 = require("firebase-admin/firestore");
const app_1 = require("firebase-admin/app");
const node_crypto_1 = require("node:crypto");
const productionConfig_1 = require("./productionConfig");
const safeDiagnostics_1 = require("./safeDiagnostics");
function allowedOrigins() {
    if (!(0, productionConfig_1.isProduction)())
        return [/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
    const configured = process.env.ALLOWED_WEB_ORIGIN;
    if (!configured)
        throw new Error('ALLOWED_WEB_ORIGIN is required in production');
    const url = new URL(configured);
    if (url.protocol !== 'https:' || url.origin !== configured || url.username || url.password ||
        url.hostname === 'localhost' || url.hostname.endsWith('.localhost') || /^[\d.]+$/.test(url.hostname) || url.hostname.includes(':')) {
        throw new Error('ALLOWED_WEB_ORIGIN requires a production HTTPS origin');
    }
    return [configured];
}
exports.allowedOrigins = allowedOrigins;
function checkOrigin(origin) {
    const allowed = allowedOrigins();
    if (origin && !allowed.some(entry => typeof entry === 'string' ? entry === origin : entry.test(origin))) {
        throw new https_1.HttpsError('permission-denied', 'Origin not allowed');
    }
}
exports.checkOrigin = checkOrigin;
async function authorizeProject(uid, projectId) {
    if (typeof projectId !== 'string' || !projectId || projectId.includes('/'))
        throw new https_1.HttpsError('invalid-argument', 'Invalid project identifier');
    if (!(0, app_1.getApps)().length)
        (0, app_1.initializeApp)();
    const snapshot = await (0, firestore_1.getFirestore)().collection('projects').doc(projectId).get();
    const project = snapshot.data();
    if (!snapshot.exists)
        throw new https_1.HttpsError('not-found', 'Project not found');
    if ((project === null || project === void 0 ? void 0 : project.ownerId) !== uid && !(Array.isArray(project === null || project === void 0 ? void 0 : project.collaborators) && (project === null || project === void 0 ? void 0 : project.collaborators.some((c) => (c === null || c === void 0 ? void 0 : c.userId) === uid && c.role === 'editor')))) {
        throw new https_1.HttpsError('permission-denied', 'Project access denied');
    }
}
exports.authorizeProject = authorizeProject;
async function authorizeRequest(request) {
    var _a, _b, _c, _d, _e, _f, _g, _h;
    checkOrigin((_b = (_a = request.rawRequest) === null || _a === void 0 ? void 0 : _a.headers) === null || _b === void 0 ? void 0 : _b.origin);
    const uid = (_c = request.auth) === null || _c === void 0 ? void 0 : _c.uid;
    if (typeof uid !== 'string' || !uid)
        throw new https_1.HttpsError('unauthenticated', 'Authentication required');
    const data = request.data;
    if (!data || typeof data !== 'object' || Array.isArray(data))
        throw new https_1.HttpsError('invalid-argument', 'Invalid request');
    if (data.userId !== undefined && data.userId !== uid)
        throw new https_1.HttpsError('permission-denied', 'Identity mismatch');
    // Check every explicit project reference; never silently prefer one conflicting id.
    const ids = [data.projectId, (_d = data.request) === null || _d === void 0 ? void 0 : _d.projectId, (_e = data.projectContext) === null || _e === void 0 ? void 0 : _e.projectId, (_f = data.context) === null || _f === void 0 ? void 0 : _f.projectId].filter(id => id !== undefined);
    for (const id of new Set(ids))
        await authorizeProject(uid, id);
    // A Firebase blueprint URL must not bypass its project's ownership check.
    for (const image of [data.planImageUrl, (_g = data.planImage) === null || _g === void 0 ? void 0 : _g.url, (_h = data.projectContext) === null || _h === void 0 ? void 0 : _h.planImageUrl]) {
        if (typeof image !== 'string')
            continue;
        let url;
        try {
            url = new URL(image);
        }
        catch (_j) {
            throw new https_1.HttpsError('invalid-argument', 'Invalid image reference');
        }
        const match = url.pathname.match(/\/o\/(.+)$/);
        if (!match)
            continue;
        let path;
        try {
            path = decodeURIComponent(match[1]);
        }
        catch (_k) {
            throw new https_1.HttpsError('invalid-argument', 'Invalid image reference');
        }
        const parts = path.split('/');
        if (parts[0] === 'projects' && parts[2] === 'plans')
            await authorizeProject(uid, parts[1]);
        else if (parts[0] !== 'construction-plans' || parts[1] !== uid)
            throw new https_1.HttpsError('permission-denied', 'Image access denied');
    }
    return uid;
}
exports.authorizeRequest = authorizeRequest;
function onCall(options, handler) {
    return (0, https_1.onCall)(Object.assign(Object.assign({}, options), { cors: allowedOrigins() }), async (request) => {
        const request_id = (0, node_crypto_1.randomUUID)();
        let failure_stage = 'authorization';
        try {
            await authorizeRequest(request);
            failure_stage = 'processing';
            (0, safeDiagnostics_1.safeLog)('callable.accepted', { request_id });
            return await handler(request);
        }
        catch (error) {
            (0, safeDiagnostics_1.safeLog)('callable.failed', error, { request_id, failure_stage });
            if (error instanceof https_1.HttpsError) {
                // SDK/category semantics preserved; arbitrary message/details never escape.
                const messages = { unauthenticated: 'Authentication required', 'permission-denied': 'Not authorized', 'invalid-argument': 'Invalid request', 'not-found': 'Resource not found' };
                throw new https_1.HttpsError(error.code, messages[error.code] || 'Operation failed');
            }
            throw new https_1.HttpsError('internal', 'Operation failed');
        }
    });
}
exports.onCall = onCall;
//# sourceMappingURL=functionSecurity.js.map
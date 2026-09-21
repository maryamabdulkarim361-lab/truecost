"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.safeLog = exports.safeErrorMessage = exports.safeDiagnostics = void 0;
/** Deliberately never serializes an exception, message, body, headers, or URL. */
const codes = new Set(['unauthenticated', 'permission-denied', 'invalid-argument', 'not-found', 'already-exists', 'failed-precondition', 'resource-exhausted', 'deadline-exceeded', 'unavailable', 'internal']);
function safeDiagnostics(value) {
    var _a, _b, _c;
    const result = { category: 'internal', reason: 'unclassified_error' };
    if (!value || typeof value !== 'object')
        return result;
    const e = value;
    const status = (_b = (_a = e.status) !== null && _a !== void 0 ? _a : e.statusCode) !== null && _b !== void 0 ? _b : (_c = e.response) === null || _c === void 0 ? void 0 : _c.status;
    if (typeof status === 'number' && Number.isInteger(status) && status >= 400 && status <= 599) {
        result.http_status = status;
        result.reason = 'provider_http_error';
        result.category = status === 401 || status === 403 ? 'provider_auth' : status === 429 ? 'rate_limit' : status >= 500 ? 'provider_unavailable' : 'provider_error';
    }
    if (typeof e.code === 'string' && codes.has(e.code))
        result.category = e.code;
    if (['ETIMEDOUT', 'ECONNABORTED'].includes(e.code) || e.name === 'TimeoutError') {
        result.category = 'timeout';
        result.reason = 'timeout';
    }
    if (['ECONNRESET', 'ECONNREFUSED', 'ENOTFOUND'].includes(e.code)) {
        result.category = 'network';
        result.reason = 'transport_error';
    }
    return result;
}
exports.safeDiagnostics = safeDiagnostics;
function safeErrorMessage(_error) { return 'Operation failed'; }
exports.safeErrorMessage = safeErrorMessage;
function safeLog(event, ...evidence) {
    // Events are static source labels. Arbitrary strings and argument objects are
    // not copied. Preserve only recognized error evidence and a generated UUID.
    const fields = { event: /^(?:(?:aiCommand|annotationQuantifier|enhancedCsiMapper|annotationCheckAgent|clarificationAgent|enhancedInference|estimatePipelineOrchestrator|estimationPipeline|globalMaterials|materialEstimateCommand|priceComparison|pricing|projectDeletion|sagemakerInvoke|sendContactEmail)\.(?:log|info|warn|error|provider_http)|callable\.(?:accepted|failed)|provider\.failure)$/.test(event) ? event : 'operation' };
    for (const value of evidence) {
        if (value && typeof value === 'object') {
            const diagnostic = safeDiagnostics(value);
            if (diagnostic.reason !== 'unclassified_error' || diagnostic.category !== 'internal')
                Object.assign(fields, diagnostic);
            const record = value;
            for (const name of ['duration_ms', 'completedProducts', 'totalProducts', 'count']) {
                if (typeof record[name] === 'number' && Number.isFinite(record[name]))
                    fields[name] = record[name];
            }
            if (['authorization', 'provider_request', 'response_processing', 'processing'].includes(record.failure_stage))
                fields.failure_stage = record.failure_stage;
            const id = record.request_id;
            if (typeof id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(id))
                fields.request_id = id;
        }
    }
    console.log(JSON.stringify(fields));
}
exports.safeLog = safeLog;
//# sourceMappingURL=safeDiagnostics.js.map
"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.corsOrigins = exports.getCorsOrigins = void 0;
/** Compatibility export; all endpoints share the same fail-closed origin policy. */
const functionSecurity_1 = require("./functionSecurity");
exports.getCorsOrigins = functionSecurity_1.allowedOrigins;
exports.corsOrigins = (0, exports.getCorsOrigins)();
//# sourceMappingURL=corsConfig.js.map
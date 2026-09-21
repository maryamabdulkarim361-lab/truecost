"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.isOpenAIEnabled = void 0;
/** Runtime guard for the comparison paths; never reads the key when disabled. */
function isOpenAIEnabled(env = process.env) {
    const disabled = ['true', '1', 'yes', 'on'].includes((env.DISABLE_OPENAI || '').trim().toLowerCase());
    return !disabled && Boolean(env.OPENAI_API_KEY);
}
exports.isOpenAIEnabled = isOpenAIEnabled;
//# sourceMappingURL=openaiAvailability.js.map
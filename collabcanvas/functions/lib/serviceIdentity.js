"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.verifyPricingService = exports.pricingIdentityConfig = void 0;
/** Google service identity only; never Firebase browser identity or shared keys. */
const google_auth_library_1 = require("google-auth-library");
function pricingIdentityConfig() {
    const audience = process.env.PRICING_SERVICE_URL || '';
    const principal = process.env.PRICING_SERVICE_INVOKER || '';
    const url = new URL(audience);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
        url.port || url.hostname === 'localhost' || url.hostname.endsWith('.localhost') ||
        /^[\d.]+$/.test(url.hostname) || url.hostname.includes(':') || audience.includes('collabcanvas-dev') ||
        !/^[a-z][a-z0-9-]*@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com$/.test(principal)) {
        throw new Error('Pricing service configuration required');
    }
    return { audience, principal };
}
exports.pricingIdentityConfig = pricingIdentityConfig;
async function verifyPricingService(header, verify) {
    const { audience, principal } = pricingIdentityConfig();
    if (typeof header !== 'string' || !/^Bearer \S+$/i.test(header))
        throw new Error('Service authentication required');
    const token = header.split(' ')[1];
    const verifier = verify || (async (idToken, target) => {
        const client = new google_auth_library_1.OAuth2Client();
        client.transporter.defaults = { timeout: 5000, retry: false };
        return (await client.verifyIdToken({ idToken, audience: target })).getPayload();
    });
    try {
        const claims = await verifier(token, audience);
        if (!claims || !['https://accounts.google.com', 'accounts.google.com'].includes(claims.iss) ||
            claims.aud !== audience || claims.email !== principal || claims.email_verified !== true || !claims.sub) {
            throw new Error();
        }
    }
    catch (_a) {
        throw new Error('Service authentication rejected');
    }
}
exports.verifyPricingService = verifyPricingService;
//# sourceMappingURL=serviceIdentity.js.map
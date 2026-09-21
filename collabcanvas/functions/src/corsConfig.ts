/** Compatibility export; all endpoints share the same fail-closed origin policy. */
import {allowedOrigins} from './functionSecurity';
export const getCorsOrigins = allowedOrigins;
export const corsOrigins = getCorsOrigins();

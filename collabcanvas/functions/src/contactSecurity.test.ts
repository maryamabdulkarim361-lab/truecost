import {afterEach,expect,it,vi} from 'vitest';
vi.mock('firebase-functions',()=>({region:()=>({runWith:()=>({https:{onRequest:(handler:unknown)=>handler}})})}));
vi.mock('resend',()=>({Resend:class {constructor(){throw new Error('Provider construction forbidden');}}}));
import {sendContactEmail} from './sendContactEmail';
afterEach(()=>vi.unstubAllEnvs());
const response=()=>{const res:any={set:vi.fn(),status:vi.fn(),send:vi.fn(),json:vi.fn()};for(const fn of Object.values(res) as any[]) fn.mockReturnValue(res);return res;};
it('allows configured contact preflight without email/API call',async()=>{
 vi.stubEnv('APP_ENV','production');vi.stubEnv('ALLOWED_WEB_ORIGIN','https://app.example.com');
 const res=response();await (sendContactEmail as any)({method:'OPTIONS',headers:{origin:'https://app.example.com'}},res);
 expect(res.status).toHaveBeenCalledWith(204);expect(res.set).toHaveBeenCalledWith(expect.objectContaining({'Access-Control-Allow-Origin':'https://app.example.com'}));
});
it('rejects disallowed contact origin before provider access',async()=>{
 vi.stubEnv('APP_ENV','production');vi.stubEnv('ALLOWED_WEB_ORIGIN','https://app.example.com');
 const res=response();await (sendContactEmail as any)({method:'POST',headers:{origin:'https://evil.example'},body:{}},res);
 expect(res.status).toHaveBeenCalledWith(403);
});

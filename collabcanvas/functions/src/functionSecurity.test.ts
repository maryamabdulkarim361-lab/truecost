import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {Socket} from 'node:net';
const state=vi.hoisted(()=>({project:{ownerId:'owner',collaborators:[{userId:'editor',role:'editor'},{userId:'viewer',role:'viewer'}]},reads:vi.fn()}));
vi.mock('firebase-functions/v2/https',()=>({onCall:(_options:unknown,handler:unknown)=>handler,HttpsError:class extends Error {code:string;constructor(code:string,message:string){super(message);this.code=code;}}}));
vi.mock('firebase-admin/app',()=>({getApps:()=>[{}],initializeApp:vi.fn()}));
vi.mock('firebase-admin/firestore',()=>({getFirestore:()=>({collection:()=>({doc:()=>({get:async()=>{state.reads();return {exists:true,data:()=>state.project};}})})})}));
import {allowedOrigins,authorizeRequest,onCall} from './functionSecurity';
import {safeDiagnostics,safeLog} from './safeDiagnostics';
beforeEach(()=>{
 vi.stubEnv('APP_ENV','production');vi.stubEnv('K_SERVICE','');vi.stubEnv('ALLOWED_WEB_ORIGIN','https://app.example.com');
 const blocked=()=>{throw new Error('Network forbidden');};
 vi.spyOn(Socket.prototype,'connect').mockImplementation(blocked);vi.stubGlobal('fetch',blocked);
 state.reads.mockClear();
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.unstubAllEnvs();});
const req=(uid:string|undefined='owner',origin='https://app.example.com',data:unknown={projectId:'p'})=>({auth:uid?{uid}:undefined,data,rawRequest:{headers:{origin}}}) as any;
it('fails closed when production origin missing',()=>{vi.stubEnv('ALLOWED_WEB_ORIGIN','');expect(()=>allowedOrigins()).toThrow();});
it.each(['*','http://app.example.com','https://app.example.com/path','https://localhost'])('rejects invalid production origin %s',origin=>{vi.stubEnv('ALLOWED_WEB_ORIGIN',origin);expect(()=>allowedOrigins()).toThrow();});
it('allows configured production origin',async()=>{expect(await authorizeRequest(req())).toBe('owner');});
it('rejects other origin before storage access',async()=>{await expect(authorizeRequest(req('owner','https://evil.example'))).rejects.toMatchObject({code:'permission-denied'});expect(state.reads).not.toHaveBeenCalled();});
it('requires auth even without browser Origin',async()=>{await expect(authorizeRequest({data:{},rawRequest:{headers:{}}} as any)).rejects.toMatchObject({code:'unauthenticated'});});
it.each(['owner','editor'])('preserves project writer %s',async uid=>{expect(await authorizeRequest(req(uid))).toBe(uid);});
it.each(['viewer','attacker'])('rejects cross-user/non-editor action %s',async uid=>{await expect(authorizeRequest(req(uid))).rejects.toMatchObject({code:'permission-denied'});});
it('does not trust body userId',async()=>{await expect(authorizeRequest(req('owner',undefined,{userId:'attacker'}))).rejects.toMatchObject({code:'permission-denied'});});
it('nested comparison request requires project authorization',async()=>{await expect(authorizeRequest(req('attacker',undefined,{request:{projectId:'p'}}))).rejects.toMatchObject({code:'permission-denied'});});
it('auth failure never reaches provider handler',async()=>{
 const provider=vi.fn();const handler=onCall({},provider) as any;
 await expect(handler({data:{},rawRequest:{headers:{}}})).rejects.toMatchObject({code:'unauthenticated'});expect(provider).not.toHaveBeenCalled();
});
it('keeps callable success semantics',async()=>{
 const handler=onCall({},async()=>({success:true})) as any;
 expect(await handler(req())).toEqual({success:true});
});
it('does not expose raw error metadata or messages',async()=>{
 const marker='SYNTHETIC_SECRET_MARKER';const log=vi.spyOn(console,'log').mockImplementation(()=>{});
 const err=Object.assign(new Error(marker),{status:503,body:{key:marker},headers:{Authorization:marker},url:`https://example.com?key=${marker}`});
 safeLog('provider.failure',err,{request_id:'12345678-1234-1234-1234-123456789012'},marker);
 expect(JSON.stringify(log.mock.calls)).not.toContain(marker);
 expect(JSON.stringify(log.mock.calls)).toContain('503');
 const handler=onCall({},async()=>{throw err;}) as any;
 await expect(handler(req())).rejects.toMatchObject({code:'internal',message:'Operation failed'});
 expect(JSON.stringify(log.mock.calls)).not.toContain(marker);
});
it.each([401,403,429,500,503])('preserves safe HTTP status %s',status=>{expect(safeDiagnostics({status,message:'SYNTHETIC_SECRET_MARKER'})).toMatchObject({http_status:status});});
it('rejects another users personal blueprint URL',async()=>{
 await expect(authorizeRequest(req('owner',undefined,{planImageUrl:'https://firebasestorage.googleapis.com/v0/b/fake/o/construction-plans%2Fattacker%2Fplan'}))).rejects.toMatchObject({code:'permission-denied'});
});
it('checks project ownership from blueprint URL',async()=>{
 await expect(authorizeRequest(req('attacker',undefined,{planImageUrl:'https://firebasestorage.googleapis.com/v0/b/fake/o/projects%2Fp%2Fplans%2Fplan'}))).rejects.toMatchObject({code:'permission-denied'});
});

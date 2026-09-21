import {onCall as firebaseOnCall, HttpsError, CallableOptions, CallableRequest} from 'firebase-functions/v2/https';
import {getFirestore} from 'firebase-admin/firestore';
import {getApps,initializeApp} from 'firebase-admin/app';
import {randomUUID} from 'node:crypto';
import {isProduction} from './productionConfig';
import {safeLog} from './safeDiagnostics';

export function allowedOrigins(): string[] | RegExp[] {
  if (!isProduction()) return [/^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/];
  const configured=process.env.ALLOWED_WEB_ORIGIN;
  if (!configured) throw new Error('ALLOWED_WEB_ORIGIN is required in production');
  const url=new URL(configured);
  if(url.protocol!=='https:' || url.origin!==configured || url.username || url.password ||
     url.hostname==='localhost' || url.hostname.endsWith('.localhost') || /^[\d.]+$/.test(url.hostname) || url.hostname.includes(':')) {
    throw new Error('ALLOWED_WEB_ORIGIN requires a production HTTPS origin');
  }
  return [configured];
}
export function checkOrigin(origin: string | undefined): void {
  const allowed=allowedOrigins();
  if(origin && !allowed.some(entry=>typeof entry==='string'?entry===origin:entry.test(origin))) {
    throw new HttpsError('permission-denied','Origin not allowed');
  }
}
export async function authorizeProject(uid: string, projectId: unknown): Promise<void> {
  if(typeof projectId!=='string'||!projectId||projectId.includes('/')) throw new HttpsError('invalid-argument','Invalid project identifier');
  if(!getApps().length) initializeApp();
  const snapshot=await getFirestore().collection('projects').doc(projectId).get();
  const project=snapshot.data();
  if(!snapshot.exists) throw new HttpsError('not-found','Project not found');
  if(project?.ownerId!==uid && !(Array.isArray(project?.collaborators)&&project?.collaborators.some((c:any)=>c?.userId===uid&&c.role==='editor'))) {
    throw new HttpsError('permission-denied','Project access denied');
  }
}
export async function authorizeRequest(request: CallableRequest<any>): Promise<string> {
  checkOrigin(request.rawRequest?.headers?.origin);
  const uid=request.auth?.uid;
  if(typeof uid!=='string' || !uid) throw new HttpsError('unauthenticated','Authentication required');
  const data=request.data;
  if(!data || typeof data!=='object' || Array.isArray(data)) throw new HttpsError('invalid-argument','Invalid request');
  if(data.userId!==undefined && data.userId!==uid) throw new HttpsError('permission-denied','Identity mismatch');
  // Check every explicit project reference; never silently prefer one conflicting id.
  const ids=[data.projectId,data.request?.projectId,data.projectContext?.projectId,data.context?.projectId].filter(id=>id!==undefined);
  for(const id of new Set(ids)) await authorizeProject(uid,id);
  // A Firebase blueprint URL must not bypass its project's ownership check.
  for (const image of [data.planImageUrl,data.planImage?.url,data.projectContext?.planImageUrl]) {
    if (typeof image!=='string') continue;
    let url:URL;
    try { url=new URL(image); } catch { throw new HttpsError('invalid-argument','Invalid image reference'); }
    const match=url.pathname.match(/\/o\/(.+)$/);
    if (!match) continue;
    let path:string;
    try { path=decodeURIComponent(match[1]); } catch { throw new HttpsError('invalid-argument','Invalid image reference'); }
    const parts=path.split('/');
    if(parts[0]==='projects' && parts[2]==='plans') await authorizeProject(uid,parts[1]);
    else if(parts[0]!=='construction-plans' || parts[1]!==uid) throw new HttpsError('permission-denied','Image access denied');
  }
  return uid;
}
export function onCall<T=any>(options: CallableOptions, handler:(request:CallableRequest<T>)=>any) {
  return firebaseOnCall<T>({...options,cors:allowedOrigins()},async request=>{
    const request_id=randomUUID();
    let failure_stage='authorization';
    try {
      await authorizeRequest(request);
      failure_stage='processing';
      safeLog('callable.accepted',{request_id});
      return await handler(request);
    } catch(error) {
      safeLog('callable.failed',error,{request_id,failure_stage});
      if(error instanceof HttpsError) {
        // SDK/category semantics preserved; arbitrary message/details never escape.
        const messages:Record<string,string>={unauthenticated:'Authentication required','permission-denied':'Not authorized','invalid-argument':'Invalid request','not-found':'Resource not found'};
        throw new HttpsError(error.code,messages[error.code]||'Operation failed');
      }
      throw new HttpsError('internal','Operation failed');
    }
  });
}

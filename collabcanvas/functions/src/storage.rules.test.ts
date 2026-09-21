/** Isolated localhost emulators only. No production bucket or credentials. */
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {beforeAll,afterAll,it} from 'vitest';
import {initializeTestEnvironment,assertFails,assertSucceeds,RulesTestEnvironment} from '@firebase/rules-unit-testing';
import {doc,setDoc} from 'firebase/firestore';
import {ref,uploadBytes,getBytes,deleteObject} from 'firebase/storage';

const firestoreRules = readFileSync(
  resolve(__dirname, '../../firestore.rules'),
  'utf8'
);

const storageRules = readFileSync(
  resolve(__dirname, '../../storage.rules'),
  'utf8'
);

let env:RulesTestEnvironment;
async function setupStep<T>(name:string, operation:Promise<T>):Promise<T> {
 let timer:ReturnType<typeof setTimeout>;
 try { return await Promise.race([operation,new Promise<never>((_,reject)=>{
  timer=setTimeout(()=>reject(new Error(`Storage setup stalled: ${name}`)),8000);
 })]); } finally { clearTimeout(timer!); }
}
const bytes=new Uint8Array([1,2,3]);
beforeAll(async()=>{
 // Storage's cross-service firestore.get uses the emulator startup project.
 // Match the dedicated security suite (--project collabcanvas-dev), so seeded
 // membership documents are visible to Storage rules in the same namespace.
 env=await setupStep('initialize rules',initializeTestEnvironment({projectId:'collabcanvas-dev',
 firestore: {
  host: '127.0.0.1',
  port: Number(process.env.STORAGE_TEST_FIRESTORE_PORT || 18181),
  rules: firestoreRules
},
storage: {
  host: '127.0.0.1',
  port: Number(process.env.STORAGE_TEST_STORAGE_PORT || 19199),
  rules: storageRules
}

}));
 await env.withSecurityRulesDisabled(async ctx=>{
  await setupStep('seed Firestore project',setDoc(doc(ctx.firestore(),'projects/p'),{ownerId:'owner',collaborators:[{userId:'editor',role:'editor'},{userId:'viewer',role:'viewer'}]}));
  for(const path of ['construction-plans/owner/plan','projects/p/plans/plan','pdfs/e/estimate.pdf']) {
   const storage=ctx.storage();
   storage.setMaxOperationRetryTime(0);
   await setupStep('seed Storage object',uploadBytes(ref(storage,path),bytes));
  }
 });
},30000);
afterAll(async()=>{if(env)await env.cleanup();});
it.each(['attacker',null])('denies cross-user/anonymous blueprint reads %s',async uid=>{
 const ctx=uid?env.authenticatedContext(uid):env.unauthenticatedContext();
 for(const path of ['construction-plans/owner/plan','projects/p/plans/plan']) await assertFails(getBytes(ref(ctx.storage(),path)));
});
it.each(['attacker','viewer'])('denies blueprint overwrite, upload and deletion %s',async uid=>{
 const storage=env.authenticatedContext(uid).storage();
 for(const path of ['construction-plans/owner/plan','projects/p/plans/plan']) {
  await assertFails(uploadBytes(ref(storage,path),bytes));await assertFails(deleteObject(ref(storage,path)));
 }
 await assertFails(uploadBytes(ref(storage,'projects/p/plans/new'),bytes));
});
it.each(['owner','editor'])('allows legitimate project read/write/delete %s',async uid=>{
 const storage=env.authenticatedContext(uid).storage();
 await assertSucceeds(getBytes(ref(storage,'projects/p/plans/plan')));
 const target=ref(storage,`projects/p/plans/${uid}`);
 await assertSucceeds(uploadBytes(target,bytes));await assertSucceeds(deleteObject(target));
});
it('allows viewer read only',async()=>{
 await assertSucceeds(getBytes(ref(env.authenticatedContext('viewer').storage(),'projects/p/plans/plan')));
});
it('allows personal owner and denies unknown canvas ownership',async()=>{
 const storage=env.authenticatedContext('owner').storage();
 await assertSucceeds(getBytes(ref(storage,'construction-plans/owner/plan')));
 await assertFails(uploadBytes(ref(storage,'construction-plans/arbitrary-canvas/plan'),bytes));
});
it.each(['owner','attacker',null])('denies direct report read/overwrite/delete %s',async uid=>{
 const storage=(uid?env.authenticatedContext(uid):env.unauthenticatedContext()).storage();
 const report=ref(storage,'pdfs/e/estimate.pdf');
 await assertFails(getBytes(report));await assertFails(uploadBytes(report,bytes));await assertFails(deleteObject(report));
});

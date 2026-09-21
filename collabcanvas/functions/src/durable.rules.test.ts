/** Isolated demo project; only emulator RPCs. No Functions/provider invocation. */
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {randomUUID} from 'node:crypto';
import {beforeAll,afterAll,it,expect} from 'vitest';
import {initializeTestEnvironment,assertFails,assertSucceeds,RulesTestEnvironment} from '@firebase/rules-unit-testing';
import {doc,getDoc,setDoc,updateDoc,deleteDoc} from 'firebase/firestore';
let env:RulesTestEnvironment;
const paths=['durableJobs/job','durableJobs/job/checkpoints/op','durableJobs/job/leases/lease','durableJobs/job/dispatchIntents/op'];
beforeAll(async()=>{
 if(process.env.FIRESTORE_EMULATOR_HOST!=='127.0.0.1:8081')throw new Error('Exact local emulator required');
 env=await initializeTestEnvironment({projectId:'demo-durable-rules-'+randomUUID().slice(0,8),firestore:{
  host:'127.0.0.1',port:8081,rules:readFileSync(resolve(__dirname,'../../firestore.rules'),'utf8')}});
 await env.withSecurityRulesDisabled(async context=>{
  const db=context.firestore();
  await setDoc(doc(db,'projects/project'),{ownerId:'owner',collaborators:[{userId:'editor',role:'editor'},{userId:'viewer',role:'viewer'}]});
  await setDoc(doc(db,'estimates/estimate'),{userId:'owner',durableJobId:'job',status:'processing'});
  for(const path of paths)await setDoc(doc(db,path),{ownerUid:'owner',projectId:'project',generation:1,status:'running'});
 });
},30000);
afterAll(async()=>{if(env){try{await env.clearFirestore();}finally{await env.cleanup();}}});
for(const uid of ['owner','editor','viewer','attacker',null]){
 it.each(paths)(`blocks ${uid ?? 'anonymous'} durable writes at %s`,async path=>{
  const db=(uid ? env.authenticatedContext(uid) : env.unauthenticatedContext()).firestore();
  await assertFails(updateDoc(doc(db,path),{generation:999,currentOperation:'final',leaseOwner:'forged',dispatchIntent:{status:'pending'}}));
  await assertFails(setDoc(doc(db,path+'-forged'),{ownerUid:uid,status:'final'}));
  await assertFails(deleteDoc(doc(db,path)));
  await assertFails(getDoc(doc(db,path)));
 });
}
it('owner still reads estimate projection; Admin writes are privileged',async()=>{
 const db=env.authenticatedContext('owner').firestore();
 await assertSucceeds(getDoc(doc(db,'estimates/estimate')));
 await assertFails(updateDoc(doc(db,'estimates/estimate'),{totalCost:1}));
 await env.withSecurityRulesDisabled(async context=>{
  await updateDoc(doc(context.firestore(),'durableJobs/job'),{generation:2});
  expect((await getDoc(doc(context.firestore(),'durableJobs/job'))).data()?.generation).toBe(2);
 });
});

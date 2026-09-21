/** Only localhost emulator traffic, isolated demo project; never production data. */
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {beforeAll,afterAll,it} from 'vitest';
import {initializeTestEnvironment,assertFails,assertSucceeds,RulesTestEnvironment} from '@firebase/rules-unit-testing';
import {doc,getDoc,setDoc,updateDoc,deleteDoc} from 'firebase/firestore';
let env:RulesTestEnvironment;
beforeAll(async()=>{
 const host=process.env.FIRESTORE_EMULATOR_HOST || '127.0.0.1:8081';
 if(!['127.0.0.1:8081','127.0.0.1:18181'].includes(host)) throw new Error('Local test emulator required');
 env=await initializeTestEnvironment({projectId:'demo-security-hardening-v1',firestore:{host:'127.0.0.1',port:Number(host.split(':')[1]),rules:readFileSync(resolve(__dirname,'../../firestore.rules'),'utf8')}});
 await env.withSecurityRulesDisabled(async context=>{
  const db=context.firestore();
  await setDoc(doc(db,'projects/p'),{ownerId:'owner',collaborators:[{userId:'editor',role:'editor'},{userId:'viewer',role:'viewer'}],createdBy:'owner',createdAt:0});
  await setDoc(doc(db,'projects/p/shapes/s'),{createdBy:'owner'});
  await setDoc(doc(db,'projects/p/pipeline/status'),{status:'complete'});
  await setDoc(doc(db,'estimates/e'),{userId:'owner',status:'processing'});
  await setDoc(doc(db,'estimates/e/agentOutputs/location'),{userId:'attacker',output:{}});
 });
},30000);
afterAll(async()=>{if(env){try{await env.clearFirestore();}finally{await env.cleanup();}}});
it.each(['owner','editor','viewer'])('permits project member %s',async uid=>{
 const db=env.authenticatedContext(uid).firestore();
 await assertSucceeds(getDoc(doc(db,'projects/p')));
 await assertSucceeds(getDoc(doc(db,'projects/p/pipeline/status')));
});
it('blocks unrelated project access despite a collaborators list',async()=>{
 const db=env.authenticatedContext('attacker').firestore();
 await assertFails(getDoc(doc(db,'projects/p')));
 await assertFails(getDoc(doc(db,'projects/p/pipeline/status')));
 await assertFails(updateDoc(doc(db,'projects/p'),{name:'stolen'}));
});
it('prevents editors changing collaborator permissions',async()=>{
 const db=env.authenticatedContext('editor').firestore();
 await assertFails(updateDoc(doc(db,'projects/p'),{collaborators:[{userId:'attacker',role:'editor'}]}));
});
it('enforces estimate parent owner even when child contains another userId',async()=>{
 const owner=env.authenticatedContext('owner').firestore();
 const attacker=env.authenticatedContext('attacker').firestore();
 await assertSucceeds(getDoc(doc(owner,'estimates/e/agentOutputs/location')));
 await assertFails(getDoc(doc(attacker,'estimates/e')));
 await assertFails(getDoc(doc(attacker,'estimates/e/agentOutputs/location')));
});

it('viewer cannot edit project or pipeline',async()=>{
 const db=env.authenticatedContext('viewer').firestore();
 await assertFails(updateDoc(doc(db,'projects/p'),{name:'changed'}));
 await assertFails(updateDoc(doc(db,'projects/p/pipeline/status'),{status:'fake'}));
});

it('blocks cross-user and viewer project-shape deletion; editor succeeds',async()=>{
 for(const uid of ['attacker','viewer']) {
  await assertFails(deleteDoc(doc(env.authenticatedContext(uid).firestore(),'projects/p/shapes/s')));
 }
 await assertSucceeds(deleteDoc(doc(env.authenticatedContext('editor').firestore(),'projects/p/shapes/s')));
});

it('denies owner forgery of pipeline root, output and monetary ledger',async()=>{
 const db=env.authenticatedContext('owner').firestore();
 await assertFails(updateDoc(doc(db,'estimates/e'),{totalCost:1,status:'completed'}));
 await assertFails(setDoc(doc(db,'estimates/forged'),{userId:'owner',status:'completed'}));
 await assertFails(setDoc(doc(db,'estimates/e/agentOutputs/cost'),{output:{totalCost:1}}));
 await assertFails(setDoc(doc(db,'estimates/e/costItems/forged'),{total:1}));
 await assertFails(deleteDoc(doc(db,'estimates/e')));
});

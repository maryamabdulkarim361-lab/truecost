import {afterEach,expect,it,vi} from 'vitest';
import {getPythonPipelineUrl} from './estimatePipelineOrchestrator';
afterEach(()=>vi.unstubAllEnvs());
it('routes emulator forwarding to Python 5003',()=>{
 vi.stubEnv('FUNCTIONS_EMULATOR','true');vi.stubEnv('PYTHON_FUNCTIONS_URL','');
 expect(getPythonPipelineUrl()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline');
});
it.each(['http://127.0.0.1:5003','http://127.0.0.1:5003/collabcanvas-dev/us-central1/'])('normalizes %s',url=>{
 vi.stubEnv('PYTHON_FUNCTIONS_URL',url);
 expect(getPythonPipelineUrl()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1/start_deep_pipeline');
});
const state=vi.hoisted(()=>({writes:[] as unknown[]}));
vi.mock('firebase-functions/v2/https',()=>({onCall:(_options:unknown,handler:unknown)=>handler,HttpsError:class extends Error {code:string;constructor(code:string,message:string){super(message);this.code=code;}}}));
vi.mock('firebase-admin/app',()=>({getApps:()=>[{}],initializeApp:vi.fn()}));
vi.mock('firebase-admin/firestore',()=>{
 const ref:any={collection:()=>ref,doc:()=>ref,get:async()=>({exists:true,data:()=>({ownerId:'user',name:'Fixture'}),forEach:()=>{}}),set:async(data:unknown)=>{state.writes.push(data);},update:async(data:unknown)=>{state.writes.push(data);}};
 return {getFirestore:()=>ref,FieldValue:{serverTimestamp:()=>0}};
});
import {triggerEstimatePipeline} from './estimatePipelineOrchestrator';
const invoke=triggerEstimatePipeline as unknown as (request:unknown)=>Promise<any>;
it('requires completed clarification',async()=>{
 await expect(invoke({auth:{uid:'user'},rawRequest:{headers:{authorization:'Bearer offline-test-token'}},data:{projectId:'project'}})).rejects.toMatchObject({code:'invalid-argument'});
});
it.each([false,true])('rejects failed Python start (%s)',async(networkFailure)=>{
 vi.stubGlobal('fetch',vi.fn(async()=>{
  if(networkFailure)throw new Error('offline failure');
  return {ok:false,json:async()=>({success:false,error:{message:'Invalid'}})};
 }));
 await expect(invoke({auth:{uid:'user'},rawRequest:{headers:{authorization:'Bearer offline-test-token'}},data:{projectId:'project',clarificationOutput:{projectBrief:{projectType:'kitchen_remodel'}}}})).rejects.toMatchObject({code:'internal'});
 expect(state.writes).toContainEqual(expect.objectContaining({status:'error'}));vi.unstubAllGlobals();
});
it('forwards authenticated identity and completed clarification',async()=>{
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,json:async()=>({success:true})})));
 const result=await invoke({auth:{uid:'user'},rawRequest:{headers:{authorization:'Bearer offline-test-token'}},data:{projectId:'project',clarificationOutput:{projectBrief:{projectType:'kitchen_remodel'}}}});
 const body=JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string);
 expect(Boolean((vi.mocked(fetch).mock.calls[0][1]!.headers as Record<string,string>).Authorization)).toBe(true);
 expect(body.userId).toBe('user');expect(body.clarificationOutput.estimateId).toBe(result.pipelineId);
 expect(body.clarificationOutput.projectBrief.projectType).toBe('kitchen_remodel');vi.unstubAllGlobals();
});
it('production forwards 202 without legacy status writes',async()=> {
 for(const key of ['FUNCTIONS_EMULATOR','USE_FIREBASE_EMULATORS','DISABLE_OPENAI','FIRESTORE_EMULATOR_HOST','FIREBASE_AUTH_EMULATOR_HOST','FIREBASE_STORAGE_EMULATOR_HOST','STORAGE_EMULATOR_HOST','FIREBASE_DATABASE_EMULATOR_HOST']) vi.stubEnv(key,'');
 vi.stubEnv('APP_ENV','production');vi.stubEnv('GCLOUD_PROJECT','production-project');
 vi.stubEnv('ALLOWED_WEB_ORIGIN','https://app.example.com');vi.stubEnv('PYTHON_FUNCTIONS_URL','https://python.example.com');
 state.writes=[];
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,status:202,json:async()=>({success:true,data:{estimateId:'server-est',jobId:'job',status:'accepted'}})})));
 const result=await invoke({auth:{uid:'user'},rawRequest:{headers:{authorization:'Bearer offline-test-token'}},data:{projectId:'project',clarificationOutput:{estimateId:'stable',projectBrief:{projectType:'kitchen_remodel'}}}});
 expect(result.pipelineId).toBe('server-est');expect(state.writes).toEqual([]);
 expect(JSON.parse(vi.mocked(fetch).mock.calls[0][1]!.body as string).idempotencyKey).toBe('stable');
 vi.unstubAllGlobals();
});

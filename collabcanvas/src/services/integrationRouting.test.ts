import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {getPythonFunctionsUrl} from './pythonFunctions';
import {triggerEstimatePipeline,subscribeToPipelineProgress} from './pipelineService';
import {generatePDF} from './pdfService';
import {auth} from './firebase';
import {onSnapshot} from 'firebase/firestore';
beforeEach(()=>{
 Object.assign(auth,{currentUser:{getIdToken:vi.fn(async()=> 'offline-test-token')}});
 vi.stubEnv('VITE_USE_FIREBASE_EMULATORS','true');vi.stubEnv('VITE_FIREBASE_PROJECT_ID','collabcanvas-dev');
 vi.stubEnv('VITE_PYTHON_FUNCTIONS_URL','');vi.stubEnv('VITE_FIREBASE_FUNCTIONS_URL','http://localhost:5001');
 vi.stubGlobal('fetch',vi.fn(()=>{throw new Error('Network forbidden');}));
});
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();vi.clearAllMocks();});
it('defaults Python to 5003 independently of Firebase',()=>{
 expect(getPythonFunctionsUrl()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1');
});
it.each(['http://127.0.0.1:5003','http://127.0.0.1:5003/collabcanvas-dev/us-central1/'])('normalizes %s',url=>{
 vi.stubEnv('VITE_PYTHON_FUNCTIONS_URL',url);
 expect(getPythonFunctionsUrl()).toBe('http://127.0.0.1:5003/collabcanvas-dev/us-central1');
});
it('preserves production routing',()=>{
 vi.stubEnv('VITE_USE_FIREBASE_EMULATORS','false');
 expect(getPythonFunctionsUrl()).toBe('https://us-central1-collabcanvas-dev.cloudfunctions.net');
});
it('preserves start contract and identity',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>({success:true,data:{estimateId:'offline'}})} as Response);
 const clarification={estimateId:'offline',projectBrief:{projectType:'kitchen_remodel'}};
 expect(await triggerEstimatePipeline('project','user',clarification)).toEqual({success:true,estimateId:'offline'});
 const [url,request]=vi.mocked(fetch).mock.calls[0];
 expect(Boolean((request!.headers as Record<string,string>).Authorization)).toBe(true);
 expect(url).toContain(':5003/collabcanvas-dev/us-central1/start_deep_pipeline');
 expect(JSON.parse(request!.body as string)).toEqual({userId:'user',projectId:'project',idempotencyKey:'offline',clarificationOutput:clarification});
});
it('propagates rejection',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:false,status:400,json:async()=>({success:false,error:{message:'Invalid'}})} as Response);
 expect((await triggerEstimatePipeline('project','user',{})).success).toBe(false);
});
it('routes PDF to Python',async()=>{
 vi.mocked(fetch).mockResolvedValue({ok:true,json:async()=>({success:true,pdf_url:'offline.pdf'})} as Response);
 expect((await generatePDF('offline',true)).pdfUrl).toBe('offline.pdf');
 expect(vi.mocked(fetch).mock.calls[0][0]).toContain(':5003/collabcanvas-dev/us-central1/generate_pdf');
});
it.each(['code_compliance','timeline'])('maps %s independently',stage=>{
 const update=vi.fn();subscribeToPipelineProgress('offline',update,vi.fn());
 const calls=vi.mocked(onSnapshot).mock.calls;
 const callback=calls[calls.length-1][1] as Function;
 callback({exists:()=>true,data:()=>({status:'processing',pipelineStatus:{currentAgent:stage,completedAgents:['location','scope'],progress:40}})});
 expect(update.mock.calls[0][0].currentStage).toBe(stage);
});
it('rejects emulator settings in a production build',()=>{
 vi.stubEnv('PROD',true);expect(()=>getPythonFunctionsUrl()).toThrow();
});
it('requires explicit production HTTPS endpoint',()=>{
 vi.stubEnv('PROD',true);vi.stubEnv('VITE_USE_FIREBASE_EMULATORS','false');
 vi.stubEnv('VITE_FIREBASE_PROJECT_ID','example-production');
 expect(()=>getPythonFunctionsUrl()).toThrow();
 vi.stubEnv('VITE_PYTHON_FUNCTIONS_URL','http://127.0.0.1:5003');expect(()=>getPythonFunctionsUrl()).toThrow();
 vi.stubEnv('VITE_PYTHON_FUNCTIONS_URL','https://us-central1-example-production.cloudfunctions.net');
 expect(getPythonFunctionsUrl()).toBe('https://us-central1-example-production.cloudfunctions.net');
});

it('rejects development project in a production endpoint path',()=>{
 vi.stubEnv('PROD',true);vi.stubEnv('VITE_USE_FIREBASE_EMULATORS','false');
 vi.stubEnv('VITE_FIREBASE_PROJECT_ID','example-production');
 vi.stubEnv('VITE_PYTHON_FUNCTIONS_URL','https://example.com/collabcanvas-dev/us-central1');
 expect(()=>getPythonFunctionsUrl()).toThrow();
});

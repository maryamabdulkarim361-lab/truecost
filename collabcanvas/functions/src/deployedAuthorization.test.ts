/** Exercise actual exported handlers; providers and network must remain untouched. */
import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import {Socket} from 'node:net';
vi.mock('firebase-functions/v2/https',()=>({onRequest:(_o:unknown,h:unknown)=>h,onCall:(_o:unknown,h:unknown)=>h,HttpsError:class extends Error {code:string;constructor(c:string,m:string){super(m);this.code=c;}}}));
vi.mock('firebase-functions/params',()=>({defineSecret:()=>({value:()=>{throw new Error('Credential access forbidden');}})}));
vi.mock('firebase-admin/app',()=>({getApps:()=>[{}],initializeApp:vi.fn()}));
vi.mock('firebase-admin',()=>({apps:[{}],app:()=>({}),initializeApp:vi.fn()}));
vi.mock('firebase-admin/firestore',()=>({getFirestore:()=>({collection:()=>({doc:()=>({get:async()=>({exists:true,data:()=>({ownerId:'owner',collaborators:[]})})})})}),FieldValue:{serverTimestamp:()=>0}}));
vi.mock('openai',()=>({default:class {constructor(){throw new Error('OpenAI construction forbidden');}}}));
vi.mock('aws-sdk',()=>({default:{SageMakerRuntime:class {constructor(){throw new Error('AWS construction forbidden');}}}}));
import {aiCommand} from './aiCommand';
import {materialEstimateCommand} from './materialEstimateCommand';
import {getHomeDepotPrice} from './pricing';
import {sagemakerInvoke} from './sagemakerInvoke';
import {clarificationAgent} from './clarificationAgent';
import {estimationPipeline} from './estimationPipeline';
import {comparePrices} from './priceComparison';
import {annotationCheckAgent} from './annotationCheckAgent';
import {triggerEstimatePipeline,updatePipelineStage} from './estimatePipelineOrchestrator';
const handlers=Object.entries({aiCommand,materialEstimateCommand,getHomeDepotPrice,sagemakerInvoke,clarificationAgent,estimationPipeline,comparePrices,annotationCheckAgent,triggerEstimatePipeline,updatePipelineStage});
beforeEach(()=>{
 vi.stubEnv('APP_ENV','development');vi.stubEnv('K_SERVICE','');
 const blocked=()=>{throw new Error('Network forbidden');};vi.spyOn(Socket.prototype,'connect').mockImplementation(blocked);vi.stubGlobal('fetch',blocked);
});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();vi.unstubAllEnvs();});
it.each(handlers)('%s rejects missing identity before any provider work',async(_name,handler)=>{
 await expect((handler as any)({data:{projectId:'p'},rawRequest:{headers:{}}})).rejects.toMatchObject({code:'unauthenticated'});
});
it.each(handlers)('%s rejects cross-user project before provider work',async(_name,handler)=>{
 await expect((handler as any)({auth:{uid:'attacker'},data:{projectId:'p'},rawRequest:{headers:{}}})).rejects.toMatchObject({code:'permission-denied'});
});

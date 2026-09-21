import {it,expect,vi,afterEach} from 'vitest';
import {triggerEstimatePipeline} from './pipelineService';
vi.mock('./pythonAuth',()=>({pythonAuthHeaders:async()=>({'Content-Type':'application/json',Authorization:'Bearer offline'})}));
vi.mock('./pythonFunctions',()=>({getPythonFunctionsUrl:()=> 'http://127.0.0.1:5003/test'}));
afterEach(()=>vi.unstubAllGlobals());
it('accepts 202 server identity and never automatically retries',async()=> {
 const fetcher=vi.fn(async(..._args: any[])=>({ok:true,status:202,json:async()=>({success:true,data:{estimateId:'server-id',jobId:'job',status:'accepted'}})}));
 vi.stubGlobal('fetch',fetcher);
 const result=await triggerEstimatePipeline('p','u',{estimateId:'stable',projectBrief:{projectType:'kitchen'}});
 expect(result).toEqual({success:true,estimateId:'server-id'});
 expect(fetcher).toHaveBeenCalledTimes(1);
 expect(JSON.parse(fetcher.mock.calls[0][1].body).idempotencyKey).toBe('stable');
});
it('failure never resubmits',async()=> {
 const fetcher=vi.fn(async()=>({ok:false,status:503,json:async()=>({error:{message:'Unavailable'}})}));vi.stubGlobal('fetch',fetcher);
 expect((await triggerEstimatePipeline('p','u',{estimateId:'stable'})).success).toBe(false);
 expect(fetcher).toHaveBeenCalledTimes(1);
});
it('unchanged recreated inputs have stable idempotency',async()=> {
 const keys:string[]=[];
 vi.stubGlobal('fetch',vi.fn(async(_url,options)=> {keys.push(JSON.parse(options.body).idempotencyKey);return {ok:true,json:async()=>({success:true,data:{estimateId:'server'}})};}));
 await triggerEstimatePipeline('p','u',{projectBrief:{b:1,a:2}});
 await triggerEstimatePipeline('p','u',{projectBrief:{a:2,b:1}});
 expect(keys[0]).toBe(keys[1]);
});

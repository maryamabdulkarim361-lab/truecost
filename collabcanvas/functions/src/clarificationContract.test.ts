import {it,expect,vi} from 'vitest';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
vi.mock('./functionSecurity',()=>({onCall:()=>()=>{throw new Error('Agent invocation forbidden');},allowedOrigins:()=>[]}));
vi.mock('openai',()=>({OpenAI:class {constructor(){throw new Error('Provider forbidden');}}}));
import {readFileSync} from 'node:fs';
import * as ts from 'typescript';
// Execute the actual pure legacy builder without importing Firebase/browser code.
const legacySource=readFileSync(resolve(__dirname,'../../src/services/pipelineService.ts'),'utf8');
const legacyFunction=legacySource.slice(legacySource.indexOf('export function buildFallbackClarificationOutput')).replace('export ','');
const buildFallbackClarificationOutput=new Function(ts.transpileModule(legacyFunction,{compilerOptions:{target:ts.ScriptTarget.ES2020,module:ts.ModuleKind.None}}).outputText+';return buildFallbackClarificationOutput;')();
import {assembleClarificationOutput,createCSIScope} from './estimationPipeline';
import {computeQuantitiesFromAnnotations,buildSpaceModelFromQuantities} from './annotationQuantifier';
import {buildEnhancedCSIItems} from './enhancedCsiMapper';
import {extractProjectSpecificData,generateLayoutNarrative} from './projectSpecificExtractor';
function produce(minimum=false, incomplete=false) {
 const clarificationData={projectType:minimum?'other':'kitchen_remodel',finishLevel:'mid_range',
  location:{fullAddress:'100 Test St, Denver CO 80202',streetAddress:'100 Test St',city:'Denver',state:'CO',zipCode:incomplete?'':'80202'},
  flexibility:'flexible'};
 const scopeText=minimum?'Paint the measured room':'Kitchen remodel with cabinets and countertops';
 const quantities=computeQuantitiesFromAnnotations({scale:{pixelsPerUnit:10,unit:'feet'},
  layers:[{id:'room',name:minimum?'Room':'Kitchen',visible:true,shapeCount:1}],
  shapes:[{id:'r1',type:'polygon',label:minimum?'Room':'Kitchen',layerId:'room',x:0,y:0,w:140,h:140,points:[0,0,140,0,140,140,0,140],source:'manual'}]});
 const specific=extractProjectSpecificData(quantities,clarificationData.projectType,clarificationData,scopeText);
 return assembleClarificationOutput({estimateId:minimum?'minimum':'normal',clarificationData,scopeText,
  quantities,csiScope:createCSIScope(buildEnhancedCSIItems(quantities,{projectType:clarificationData.projectType,finishLevel:'mid_range',scopeText,clarificationData,clarificationContext:{}})),
  planImageUrl:'https://example.invalid/plan.png',spaceModel:buildSpaceModelFromQuantities(quantities),projectSpecificData:specific,
  spatialNarrative:generateLayoutNarrative(quantities,clarificationData.projectType,specific),inferenceResult:null}).fixedOutput;
}
function consume(outputs:any[]) {
 const root=resolve(__dirname,'../../..');
 const result=spawnSync(resolve(root,'functions/venv/bin/python'),['-B',resolve(root,'functions/tests/contracts/clarification_bridge.py')],{
  input:JSON.stringify(outputs),encoding:'utf8',env:{PATH:process.env.PATH,PYTHONPATH:resolve(root,'functions')},timeout:30000});
 expect(result.status,result.stderr).toBe(0);
 return JSON.parse(result.stdout.trim().split('\n').at(-1)!);
}
const request=(output:any,key=output.estimateId)=>({projectId:'project',userId:'owner',idempotencyKey:key,clarificationOutput:output});
it('actual production assembler output crosses strict Python start without agents',{timeout:30000},()=>{
 const normal=produce(); const minimum=produce(true);
 const result=consume([request(normal),request(minimum)]);
 expect(result.map((r:any)=>r.status),JSON.stringify(result)).toEqual([202,202]);
});
it('missing, malformed, legacy inputs fail closed; duplicates preserve identity',{timeout:30000},()=>{
 const normal=produce();const missing=structuredClone(normal);delete missing.cadData;
 const malformed=structuredClone(normal);(malformed.projectBrief as any).projectType='invalid';
 const changed=structuredClone(normal);(changed.projectBrief as any).scopeSummary.description='Changed user scope';
 const result=consume([request(normal),request(normal),request(changed),request(missing,'missing'),request(malformed,'malformed'),request({estimateId:'legacy',projectBrief:'legacy'})]);
 expect(result.map((r:any)=>r.status)).toEqual([202,202,409,400,400,400]);
 expect(result[0].data).toEqual(result[1].data);
});
it('verified UID controls project authorization',{timeout:30000},()=>{
 const output=produce(); const r=request(output);
 expect(consume([{...r,userId:'attacker'},{...r,projectId:'other'}]).map((v:any)=>v.status)).toEqual([403,404]);
});

it('producer review state and identity contradictions cannot start a durable job',{timeout:30000},()=>{
 const normal=produce();const review=structuredClone(normal);review.clarificationStatus='needs_review';
 const flagged=structuredClone(normal);(flagged.flags as any).userVerificationRequired=true;
 const typed=structuredClone(normal);(typed.projectBrief as any).scopeSummary.totalSqft='196';
 const project=structuredClone(normal);project.projectId='other';
 const owner=structuredClone(normal);owner.userId='attacker';
 const responses=consume([request(review,'review'),request(flagged,'flagged'),request(typed,'typed'),request(project,'project'),request(owner,'owner'),{...request(normal),estimateId:'wrong'}]);
 expect(responses.map((r:any)=>r.status)).toEqual([400,400,400,400,403,400]);
});

it('incomplete actual producer stays review-required without fabricated location',{timeout:30000},()=>{
 const output=produce(false,true);
 expect(output.clarificationStatus).toBe('needs_review');
 expect((output.projectBrief as any).location.zipCode).toBe('');
 expect(consume([request(output)])[0].status).toBe(400);
});
it('actual reachable debug fallback is rejected by strict start',{timeout:30000},()=>{
 const legacy=buildFallbackClarificationOutput({projectId:'project',estimateId:'legacy-debug'});
 expect(consume([request(legacy)])[0].status).toBe(400);
});

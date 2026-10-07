import test from 'node:test';
import assert from 'node:assert/strict';
import { readSurfaceIdentity, SURFACE_RULES } from '../surface-identity.mjs';
function element(label,visible=true){return {getClientRects:()=>visible?[{}]:[],getAttribute:(name)=>name==='aria-label'?label:null,textContent:label};}
async function identity(target,{view='knowledge',selectors={},dialogs=[]}={}){
 const old={document:globalThis.document,location:globalThis.location,getComputedStyle:globalThis.getComputedStyle};
 try{
  globalThis.location={href:`http://127.0.0.1:9000/?view=${view}&token=never-output`};
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  globalThis.document={querySelectorAll:(selector)=>selector==='[role="dialog"]'?dialogs:selectors[selector]||[],getElementById:()=>null};
  return await readSurfaceIdentity({evaluate:(fn,args)=>fn(args)},target);
 }finally{Object.assign(globalThis,old);}
}
test('wrong root failure image cannot become Pilot or Offer form coverage',async()=>{
 const result=await identity('R12',{view:'knowledge'});assert.equal(result.observedView,'knowledge');assert.equal(result.targetSurfaceConfirmed,false);
 assert.equal((await identity('S20',{view:'interview'})).targetSurfaceConfirmed,false);
});
test('actual visible dialog verifies its target; hidden or similarly named alternatives cannot',async()=>{
 assert.equal((await identity('S20',{view:'offers',dialogs:[element('录入 Offer')]})).targetSurfaceConfirmed,true);
 assert.equal((await identity('S20',{dialogs:[element('录入 Offer',false),element('录入 Offer 其他')]})).targetSurfaceConfirmed,false);
});
test('surface identity publishes only fixed IDs and allowed root names',async()=>{
 const result=await identity('S14',{view:'token-secret',dialogs:[element('personal-secret'),element('上传简历')]});
 assert.equal(result.observedView,'unknown');assert.deepEqual(result.visibleSurfaces,['S14']);
 assert.doesNotMatch(JSON.stringify(result),/personal-secret|token-secret|never-output|http/);
 assert.equal(new Set(SURFACE_RULES.map(({id})=>id)).size,44);
});

test('S24 identifies actual lightweight Haru without pretending full Pilot controls exist',async()=>{
 const result=await identity('S24',{view:'applications-list',dialogs:[element('Haru 轻量对话')]});
 assert.equal(result.targetSurfaceConfirmed,true);assert.ok(result.visibleSurfaces.includes('S24'));
 assert.equal(result.visibleSurfaces.includes('R12'),false);
});

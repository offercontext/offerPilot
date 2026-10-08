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
 assert.equal(new Set(SURFACE_RULES.map(({id})=>id)).size,45);
});

test('standalone Haru is identified without fabricating a dashboard or a full Pilot workspace',async()=>{
 const companion = 'main[aria-label="Haru 桌面小窗"]';
 const chat = SURFACE_RULES.find(({id})=>id==='S24').selector;
 const result=await identity('S24',{view:'dashboard',selectors:{[companion]:[element('Haru 桌面小窗')],[chat]:[element('Haru 对话')]}});
 assert.equal(result.targetSurfaceConfirmed,true);
 assert.equal(result.observedView,'unknown');
 assert.deepEqual(result.visibleSurfaces,['S24','S25']);
 assert.equal(result.visibleSurfaces.includes('R12'),false);
 assert.equal(result.visibleSurfaces.includes('R01'),false);
});

test('removed in-page Haru and mascot cannot satisfy installed companion coverage',async()=>{
 const result=await identity('S24',{view:'applications-list',dialogs:[element('Haru 轻量对话')],selectors:{'aside[aria-label="Haru 助手"]':[element('Haru 助手')]}});
 assert.equal(result.targetSurfaceConfirmed,false);
 assert.equal(result.visibleSurfaces.includes('S25'),false);
});

test('empty dashboard identity uses its real first-application button without requiring onboarding',async()=>{
 const selectors={button:[element('添加第一个投递')]};
 assert.equal((await identity('R01',{view:'dashboard',selectors})).targetSurfaceConfirmed,true);
 assert.equal((await identity('R01',{view:'board',selectors})).targetSurfaceConfirmed,false);
 assert.equal((await identity('R01',{view:'dashboard',selectors:{button:[element('添加第一个投递',false)]}})).targetSurfaceConfirmed,false);
 assert.equal((await identity('R01',{view:'dashboard',selectors:{button:[element('添加第一个投递 其他')]}})).targetSurfaceConfirmed,false);
});

for(const rule of SURFACE_RULES.filter(({view})=>view)) {
 test(`${rule.id} visible landmark is route-specific and rejects hidden/root-mismatched content`,async()=>{
  const selectors={[rule.selector]:[element('root')],...(rule.id==='R04'?{span:[element('待投递')]}:{})};
  assert.equal((await identity(rule.id,{view:rule.view,selectors})).targetSurfaceConfirmed,true);
  assert.equal((await identity(rule.id,{view:rule.view==='pilot'?'dashboard':'pilot',selectors})).targetSurfaceConfirmed,false);
  assert.equal((await identity(rule.id,{view:rule.view,selectors:{[rule.selector]:[element('root',false)]}})).targetSurfaceConfirmed,false);
 });
}

test('JD source clipboard surface uses the real visible JD landmark as well as editor/history dialogs', () => {
  const jd = SURFACE_RULES.find(rule => rule.id === 'S05');
  assert.equal(jd.selector, '#application-jd-text');
  assert.deepEqual(jd.dialogs, ['投递岗位资料', '岗位资料历史']);
});

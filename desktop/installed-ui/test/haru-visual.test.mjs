import test from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';
import {observeHaruVisual,assertHaruVisual,verifyHaruVisual}from'../haru-visual.mjs';
function observed({state='ready',lost=false,noContext=false,fallback=false,overflow=false,occluded=false,canvasHidden=false,backingZero=false,image=false,collapsed=false}={}) {
 const calls=[];const style={visibility:'visible',opacity:'1'};
 const element=(left,top,width,height)=>({style,parentElement:null,getClientRects:()=>[{}],getBoundingClientRect:()=>({left,top,width,height,right:left+width,bottom:top+height}),contains:()=>false});
 const portrait=element(8,46,404,100);portrait.getAttribute=()=>state;
 const canvas=element(8,46,404,overflow?650:100);canvas.parentElement=portrait;canvas.width=backingZero?0:404;canvas.height=100;
 if(canvasHidden)canvas.getClientRects=()=>[];
 canvas.getContext=kind=>{calls.push(kind);return noContext?null:{isContextLost:()=>lost};};
 const placeholder=element(80,70,240,30);placeholder.parentElement=portrait;
 portrait.querySelectorAll=selector=>selector==='canvas'?[canvas]:selector==='img'?(image?[placeholder]:[]):fallback?[placeholder]:[];
 const chat=element(8,175,404,555);const context=element(22,190,376,34);const input=element(22,630,300,54);
 const document={querySelector:selector=>selector==='.desktop-haru.expanded'?(collapsed?null:chat):collapsed&&selector!=='.desktop-haru-portrait'?null:selector==='.desktop-haru-portrait'?portrait:selector==='.desktop-haru-context'?context:selector==='#desktop-haru-input'?input:chat,
  elementFromPoint:(_x,y)=>occluded?canvas:y<300?context:input};
 const value=structuredClone(vm.runInNewContext(`(${observeHaruVisual.toString()})()`,{document,innerWidth:420,innerHeight:740,getComputedStyle:node=>node.style}));
 return{value,calls};
}
test('Haru readiness requires the real runtime, an alive context and contained unobscured visual',()=>{
 const {value,calls}=observed();assertHaruVisual(value);assert.deepEqual(calls,['webgl2']);
 assert.equal(value.imageQualityRequiresHumanReview,true);assert.equal(value.canvasWithinPortrait,true);
 assert.doesNotMatch(JSON.stringify(value),/token|contextLabel|company|data:/);
});
for(const option of ['lost','noContext','fallback','overflow','occluded','canvasHidden','backingZero','image']) {
 test(`Haru ${option} cannot pass from canvas existence or correct context text alone`,()=>{
  assert.throws(()=>assertHaruVisual(observed({[option]:true}).value));
 });
}
for(const state of ['loading','failed','hidden','unexpected-secret']) {
 test(`Haru ${state} never creates a WebGL context while inspecting an unready runtime`,()=>{
  const {value,calls}=observed({state});assert.deepEqual(calls,[]);assert.throws(()=>assertHaruVisual(value));
  assert.doesNotMatch(JSON.stringify(value),/unexpected-secret/);
 });
}
test('Haru visual failure keeps actual screenshot observation before its assertion rejects',async()=>{
 const steps=[];const value=observed({overflow:true}).value;
 const page={locator:selector=>{assert.equal(selector,'.desktop-haru-portrait[data-runtime-state="ready"]');return{waitFor:async()=>steps.push('ready')};},waitForFunction:async(_fn,_arg,options)=>assert.equal(options.timeout,5000),evaluate:async()=>value};
 await assert.rejects(verifyHaruVisual(page,async observed=>{assert.equal(observed,value);steps.push('captured');}),/visual boundary/);
 assert.deepEqual(steps,['ready','captured']);
});

test('collapsed idle still requires a healthy contained runtime but cannot pass the S24 expanded check',()=>{
 const {value}=observed({collapsed:true});assert.equal(value.expanded,false);
 assertHaruVisual(value,{requireExpanded:false});assert.throws(()=>assertHaruVisual(value),/really be expanded/);
 assert.throws(()=>assertHaruVisual(observed({collapsed:true,lost:true}).value,{requireExpanded:false}));
});
test('unready Haru preserves broken pixels and safe observations before reporting the original wait failure',async()=>{
 let captures=0;const value=observed({state:'failed',fallback:true}).value;
 const page={locator:()=>({waitFor:async()=>{throw new Error('runtime-ready timeout');}}),evaluate:async()=>value};
 await assert.rejects(verifyHaruVisual(page,async observed=>{captures++;assert.equal(observed,value);}),/runtime-ready timeout/);
 assert.equal(captures,1);
});

test('suspended animation frames reject on a host-side deadline and still retain Haru failure evidence',async()=>{
 let captures=0;const value=observed().value;
 const page={locator:()=>({waitFor:async()=>{}}),waitForFunction:async(_fn,_arg,options)=>{assert.equal(options.timeout,5000);throw new Error('bounded frame wait expired');},evaluate:async()=>value};
 await assert.rejects(verifyHaruVisual(page,async()=>{captures++;}),/bounded frame wait expired/);
 assert.equal(captures,1);
});
test('smoke failure captures the actual Haru window before app teardown even when a transition prerequisite fails',async()=>{
 const fs=await import('node:fs/promises');const source=await fs.readFile(new URL('../smoke.mjs',import.meta.url),'utf8');
 const start=source.indexOf('} catch (error) {\n  // Keep failures failed');
 const capture=source.indexOf("report.haruFailureScreenshot = 'screens/haru-failure.png'",start);
 const close=source.indexOf('current.app.close()',start);
 assert.ok(start>0 && capture>start && close>capture);
 assert.match(source.slice(start,close),/current\.haru\.screenshot\([^]*timeout: 15000/);
});

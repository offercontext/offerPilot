import test from 'node:test';import assert from 'node:assert/strict';import vm from 'node:vm';
import {measureControlHit}from'../control-hit.mjs';
function hit({receiver='target',pointer=true,hidden=false,outside=false,disabled=false}={}) {
 const style={visibility:'visible',opacity:'1',pointerEvents:pointer?'auto':'none'};
 const ancestor={parentElement:null,style:{...style,opacity:hidden?'0':'1'}};
 const rect={left:outside?-40:784,top:16,width:24,height:24};
 const node={disabled,parentElement:ancestor,style,getBoundingClientRect:()=>rect,getClientRects:()=>[rect],contains:()=>false,
   outerHTML:'private-token-should-not-be-retained',textContent:'private user value'};
 const top=receiver==='target'?node:receiver==='none'?null:{closest:selector=>receiver==='drawer-mask'?selector==='.ant-drawer-mask':receiver==='other-dialog-element'?selector==='[role="dialog"]':receiver==='other-button'?selector==='button':false};
 const context={node,innerWidth:1280,innerHeight:900,getComputedStyle:value=>value.style,document:{elementFromPoint:()=>top}};
 return structuredClone(vm.runInNewContext(`(${measureControlHit.toString()})(node)`,context));
}
test('control hit evidence distinguishes genuine target and bounded blockers without DOM text',()=>{
 for(const receiver of ['target','none','drawer-mask','other-dialog-element','other-button','other-element']){
  const result=hit({receiver});assert.equal(result.receiver,receiver);assert.equal(result.visible,true);assert.equal(result.inViewport,true);
  assert.doesNotMatch(JSON.stringify(result),/private|token|HTML|textContent/);
 }
});
test('hidden ancestry, pointer policy, disabled state and offscreen center remain observable',()=>{
 assert.equal(hit({hidden:true}).ancestorHidden,true);
 assert.equal(hit({pointer:false}).pointerEventsEnabled,false);
 assert.equal(hit({pointer:false}).ancestorPointerDisabled,true);
 assert.equal(hit({disabled:true}).enabled,false);
 assert.equal(hit({outside:true}).inViewport,false);
});

test('scroll evidence distinguishes a locally scrolled pane from document overflow',async()=>{
 const {measureScrollableAncestors}=await import('../control-hit.mjs');
 const root={parentElement:null,scrollWidth:900,clientWidth:900,scrollLeft:0,clientLeft:0,clientTop:0,clientHeight:689,getBoundingClientRect:()=>({left:0,top:0}),css:{overflowX:'auto'}};
 const pane={parentElement:root,scrollWidth:1040,clientWidth:670,scrollLeft:330,clientLeft:0,clientTop:0,clientHeight:604,getBoundingClientRect:()=>({left:216,top:85}),css:{overflowX:'auto',overflowY:'auto'}};
 const node={parentElement:pane,getBoundingClientRect:()=>({left:640,right:850,top:500,bottom:540})};
 const context={node,innerWidth:900,innerHeight:689,getComputedStyle:value=>value.css,
  document:{scrollingElement:root,documentElement:root,body:{scrollWidth:900}}};
 const result=structuredClone(vm.runInNewContext(`(${measureScrollableAncestors.toString()})(node)`,context));
 assert.equal(result.controlFullyWithinViewport,true);assert.equal(result.controlFullyWithinScrollableBounds,true);assert.equal(result.documentWidth,900);
 assert.deepEqual(result.scrollers,[{depth:1,scrollLeft:330,clientWidth:670,scrollWidth:1040,documentScroller:false}]);
 pane.clientWidth=620;
 const clipped=structuredClone(vm.runInNewContext(`(${measureScrollableAncestors.toString()})(node)`,context));
 assert.equal(clipped.controlFullyWithinViewport,true);assert.equal(clipped.controlFullyWithinScrollableBounds,false);
 root.scrollWidth=1100;root.scrollLeft=100;
 const overflow=structuredClone(vm.runInNewContext(`(${measureScrollableAncestors.toString()})(node)`,context));
 assert.equal(overflow.documentWidth,1100);assert.equal(overflow.scrollers[1].documentScroller,true);
});

test('real wheel observer requires movement and the requested edge, never an already-nonzero offset',async()=>{
 const {waitForHorizontalWheel}=await import('../control-hit.mjs');
 let time=0;const container={scrollLeft:0,scrollWidth:764,clientWidth:669};const node={parentElement:container};
 const context={node,args:{depth:1,previous:0,direction:1,timeoutMs:5},performance:{now:()=>time},
  requestAnimationFrame:callback=>{time++;container.scrollLeft=95;callback();}};
 const result=structuredClone(await vm.runInNewContext(`(${waitForHorizontalWheel.toString()})(node,args)`,context));
 assert.equal(result.before,0);assert.equal(result.after,95);assert.equal(result.direction,1);
 context.args={depth:1,previous:95,direction:1,timeoutMs:2};time=0;
 await assert.rejects(vm.runInNewContext(`(${waitForHorizontalWheel.toString()})(node,args)`,context),/did not move/);
 context.args={depth:1,previous:0,direction:1,timeoutMs:2};time=0;
 context.requestAnimationFrame=callback=>{time++;container.scrollLeft=30;callback();};container.scrollLeft=0;
 await assert.rejects(vm.runInNewContext(`(${waitForHorizontalWheel.toString()})(node,args)`,context),/requested edge/);
});

test('no-overflow evidence measures complete horizontal card bounds, including clipping ancestors',async()=>{
 const {measureScrollableAncestors}=await import('../control-hit.mjs');
 const pane={parentElement:null,scrollWidth:669,clientWidth:669,clientHeight:604,clientLeft:0,clientTop:0,scrollLeft:0,
  getBoundingClientRect:()=>({left:216,top:85}),css:{overflowX:'auto',overflowY:'auto'}};
 let secondRight=880;const workspace={querySelectorAll:()=>cards};
 const card=(left,right)=>({parentElement:pane,closest:()=>workspace,getClientRects:()=>[{}],getBoundingClientRect:()=>({left,right:right()})});
 const cards=[card(230,()=>530),card(550,()=>secondRight)];
 const node={parentElement:pane,closest:()=>cards[1],getBoundingClientRect:()=>({left:635,right:780,top:360,bottom:400})};
 const context={node,innerWidth:900,innerHeight:689,getComputedStyle:value=>value.css,
  document:{scrollingElement:{},documentElement:{scrollWidth:900},body:{scrollWidth:900}}};
 const result=structuredClone(vm.runInNewContext(`(${measureScrollableAncestors.toString()})(node)`,context));
 assert.equal(result.cardCount,2);assert.equal(result.allCardsHorizontallyVisible,true);assert.deepEqual(result.scrollers,[]);
 secondRight=899;const clipped=structuredClone(vm.runInNewContext(`(${measureScrollableAncestors.toString()})(node)`,context));
 assert.equal(clipped.controlFullyWithinViewport,true);assert.equal(clipped.allCardsHorizontallyVisible,false);
});

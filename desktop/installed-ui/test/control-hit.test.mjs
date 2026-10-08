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

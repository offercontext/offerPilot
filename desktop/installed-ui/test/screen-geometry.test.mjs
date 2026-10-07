import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { measureScreenGeometry } from '../screen-geometry.mjs';
function measure({clip=false, axis='Y', menu=false, disabled=false, outside=false}={}) {
  const bounds = outside ? {left:20,top:-20,width:20,height:10} : {left:20,top:40,width:20,height:20};
  const ancestor = {parentElement:null,getBoundingClientRect:()=>({left:0,right:100,top:80,bottom:200}),style:{visibility:'visible',opacity:'1',overflowX:axis==='X'&&clip?'hidden':'visible',overflowY:axis==='Y'&&clip?'auto':'visible'}};
  const element={disabled,parentElement:ancestor,closest:()=>null,getClientRects:()=>[bounds],getBoundingClientRect:()=>bounds,style:{visibility:'visible',opacity:'1'}};
  const scope={innerWidth:1280,innerHeight:900,window:{innerWidth:1280,innerHeight:900},getComputedStyle:n=>n.style,
    document:{querySelectorAll:()=>[element],documentElement:{scrollWidth:1280,dataset:{theme:'dark'}},body:{scrollWidth:1280},elementFromPoint:()=>({closest:selector=>selector==='[role="menu"]'?menu:true})}};
  return vm.runInNewContext(`(${measureScreenGeometry.toString()})()`,scope).haruCoveredControls;
}
test('genuinely visible control under Haru remains an occlusion failure',()=>assert.equal(measure(),1));
test('center outside scroll ancestor clipping is not a visible control',()=>assert.equal(measure({clip:true}),0));
test('unrelated axis clipping cannot hide a genuine overlap',()=>assert.equal(measure({clip:true,axis:'X'}),1));
test('disabled controls, out-of-viewport centers and explicit menus are excluded',()=>{
 assert.equal(measure({disabled:true}),0);assert.equal(measure({outside:true}),0);assert.equal(measure({menu:true}),0);
});

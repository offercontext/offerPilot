import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { measureScreenGeometry } from '../screen-geometry.mjs';
function measure({clip=false, axis='Y', menu=false, disabled=false, outside=false}={}) {
  const bounds = outside ? {left:20,top:-20,width:20,height:10} : {left:20,top:40,width:20,height:20};
  const ancestor = {parentElement:null,getBoundingClientRect:()=>({left:0,right:100,top:80,bottom:200}),style:{visibility:'visible',opacity:'1',overflowX:axis==='X'&&clip?'hidden':'visible',overflowY:axis==='Y'&&clip?'auto':'visible'}};
  const element={disabled,parentElement:ancestor,closest:()=>null,getClientRects:()=>[bounds],getBoundingClientRect:()=>bounds,style:{visibility:'visible',opacity:'1'}};
  const scope={innerWidth:1280,innerHeight:900,window:{innerWidth:1280,innerHeight:900},getComputedStyle:n=>n.style,
    document:{querySelectorAll:selector=>selector.startsWith('[data-kanban')?[]:[element],documentElement:{scrollWidth:1280,dataset:{theme:'dark'}},body:{scrollWidth:1280},elementFromPoint:()=>({closest:selector=>selector==='[role="menu"]'?menu:true})}};
  return vm.runInNewContext(`(${measureScreenGeometry.toString()})()`,scope).haruCoveredControls;
}
test('genuinely visible control under Haru remains an occlusion failure',()=>assert.equal(measure(),1));
test('center outside scroll ancestor clipping is not a visible control',()=>assert.equal(measure({clip:true}),0));
test('unrelated axis clipping cannot hide a genuine overlap',()=>assert.equal(measure({clip:true,axis:'X'}),1));
test('disabled controls, out-of-viewport centers and explicit menus are excluded',()=>{
 assert.equal(measure({disabled:true}),0);assert.equal(measure({outside:true}),0);assert.equal(measure({menu:true}),0);
});

function kanban({ overflow = false, clipped = false, cardClipped = false, unowned = false, outerScroll = true } = {}) {
  const box = { left: 240, top: 210, right: 440, bottom: 850, width: 200, height: 640 };
  const control = { getClientRects: () => [{}], getBoundingClientRect: () => ({ left: 254, right: clipped ? 454 : 420 }), style: { visibility: 'visible' },
    closest: () => unowned ? null : ({getBoundingClientRect:()=>({left:250,right:cardClipped?410:430})}) };
  const column = { getClientRects: () => [box], getBoundingClientRect: () => box, scrollWidth: overflow ? 240 : 200,
    clientWidth: 200, clientLeft: 0, querySelectorAll: () => [control], style: { visibility: 'visible' } };
  const scope = { innerWidth: 900, innerHeight: 900, window: { innerWidth: 900, innerHeight: 900 }, getComputedStyle: node => node.style,
    document: { querySelectorAll: selector => selector.startsWith('[data-kanban') ? [column] : [],
      documentElement: { scrollWidth: 900, dataset: { theme: 'dark' } }, body: { scrollWidth: 900 },
      outerBoard: { scrollWidth: outerScroll ? 1440 : 636, clientWidth: 636 } } };
  return vm.runInNewContext(`(${measureScreenGeometry.toString()})()`, scope);
}
test('intentional whole-board horizontal scroll does not fail contained column/card geometry', () => {
  const result = kanban();
  assert.equal(result.kanbanColumnCount, 1);
  assert.equal(result.kanbanColumnHorizontalOverflow, 0);
  assert.equal(result.kanbanControlsOutsideColumn, 0);
});
test('local column overflow remains visible even when document width is correct', () => {
  const result = kanban({ overflow: true });
  assert.equal(result.documentWidth, result.width);
  assert.equal(result.kanbanColumnHorizontalOverflow, 1);
});
test('a clipped card control fails independently of scrollWidth or intentional outer scroll', () => {
  const result = kanban({ clipped: true });
  assert.equal(result.kanbanColumnHorizontalOverflow, 0);
  assert.equal(result.kanbanControlsOutsideColumn, 1);
  assert.doesNotMatch(JSON.stringify(result), /company|record|text|label/);
});

test('card footer control cannot extend beyond its card even when the column still contains it',()=>{
 const result=kanban({cardClipped:true});
 assert.equal(result.kanbanControlsOutsideColumn,0);
 assert.equal(result.kanbanControlsOutsideCard,1);
});

test('missing stable card ownership marker cannot silently skip control boundary evidence',()=>{
 assert.equal(kanban({unowned:true}).kanbanUnownedControls,1);
});

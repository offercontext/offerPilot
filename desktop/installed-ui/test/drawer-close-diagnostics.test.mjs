import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { installDrawerCloseObservation, readDrawerCloseObservation, drawerCloseFailure } from '../drawer-close-diagnostics.mjs';
function fixture({ receiver = 'target', finiteAnimations = 0, failSnapshot = false } = {}) {
  const listeners = new Map();
  const base = { visibility:'visible',opacity:'1',pointerEvents:'auto',position:'relative',zIndex:'auto',transform:'none' };
  const element = (name, css = {}) => ({name, parentElement:null, css:{...base,...css},isConnected:true,
    getBoundingClientRect:()=>({left:784,top:16,width:24,height:24}),getClientRects:()=>[{}],
    getAnimations:()=>Array.from({length:finiteAnimations},()=>({playState:'running',effect:{getTiming:()=>({iterations:1})}})),
    contains:()=>false,closest(selector){for(let current=this;current;current=current.parentElement)if(current.selectors?.includes(selector))return current;return null;},
    textContent:'private source text',outerHTML:'<secret-token />'});
  const root=element('root'),body=element('body'),drawer=element('drawer',{pointerEvents:'none'});
  drawer.selectors=['.ant-drawer'];drawer.classList={contains:value=>value==='ant-drawer-open'};
  const node=element('close');node.parentElement=drawer;
  const topbar=element('topbar',{position:'fixed',zIndex:'1001'});topbar.selectors=['.op-topbar'];
  const mask=element('mask');mask.selectors=['.ant-drawer-mask'];
  const receivers={target:node,topbar,mask,body,root};
  const doc={documentElement:root,body,visibilityState:'visible',hasFocus:()=>true,
    elementFromPoint:()=>receivers[receiver]??null,elementsFromPoint:()=>[receivers[receiver]??body,node,drawer],
    addEventListener:(type,fn,capture)=>{assert.equal(capture,true);listeners.set(type,fn);},
    removeEventListener:(type,fn,capture)=>{assert.equal(capture,true);assert.equal(listeners.get(type),fn);listeners.delete(type);}};
  node.ownerDocument=doc;
  let reads=0;
  if(failSnapshot)node.getBoundingClientRect=()=>{if(++reads>0)throw new Error('layout snapshot failed');};
  const context=vm.createContext({window:{},node,args:{key:'safe-key'},innerWidth:1280,innerHeight:900,getComputedStyle:node=>node.css});
  const install=()=>structuredClone(vm.runInContext(`(${installDrawerCloseObservation.toString()})(node,args)`,context));
  const read=(dispose=false)=>{context.args={key:'safe-key',dispose};return structuredClone(vm.runInContext(`(${readDrawerCloseObservation.toString()})(args)`,context));};
  return {install,read,listeners,context,emit:(type,target=node)=>listeners.get(type)({type,isTrusted:true,target,clientX:796,clientY:28,button:0})};
}
test('drawer diagnostics separate real hit receivers from a normal pointer-disabled drawer ancestor',()=>{
  const f=fixture();const first=f.install();
  assert.equal(first.centerReceiver,'target');assert.equal(first.ancestors[0].pointerEvents,'none');
  assert.equal(first.control.pointerEvents,'auto');assert.equal(first.drawerOpenClass,true);
  f.emit('pointerdown');f.emit('pointerup');f.emit('click');
  const last=f.read(true);assert.deepEqual(last.events.map(e=>e.type),['pointerdown','pointerup','click']);
  assert.ok(last.events.every(e=>e.trusted&&e.target==='target'));
  assert.equal(f.listeners.size,0);assert.equal(f.context.window['safe-key'],undefined);
  assert.doesNotMatch(JSON.stringify(last),/private|secret|textContent|outerHTML|className/);
});
test('bounded hit stack exposes fixed chrome/mask types, CSS geometry and live finite animation counts',()=>{
  for(const [receiver,kind] of [['topbar','app-topbar'],['mask','drawer-mask'],['body','document-body'],['root','document-root']]){
    const f=fixture({receiver,finiteAnimations:2});const result=f.install();
    assert.equal(result.centerReceiver,kind);assert.equal(result.hitStack[0].kind,kind);
    assert.equal(result.control.runningFiniteAnimations,2);assert.equal(result.control.x,784);assert.equal(result.control.y,16);
    f.read(true);
  }
});
test('pointer observations are capped and disposal occurs even if the terminal snapshot fails',()=>{
  const f=fixture();f.install();f.emit('pointermove', { privateText: 'never return this value' });for(let i=0;i<29;i++)f.emit('pointermove');
  assert.equal(f.read().pointerMoves.length,4);assert.equal(f.read().droppedPointerMoves,26);
  f.emit('pointerdown');f.emit('pointerup');f.emit('click');
  assert.deepEqual(f.read().events.map(event=>event.type),['pointerdown','pointerup','click']);
  for(let i=0;i<20;i++)f.emit('click');assert.equal(f.read().events.length,12);assert.equal(f.read().droppedEvents,11);
  f.context.node.getBoundingClientRect=()=>{throw new Error('detached layout unavailable');};
  assert.throws(()=>f.read(true),/layout unavailable/);assert.equal(f.listeners.size,0);assert.equal(f.context.window['safe-key'],undefined);
  const failed=fixture({failSnapshot:true});assert.throws(failed.install,/layout snapshot/);
  assert.equal(failed.listeners.size,0);assert.equal(failed.context.window['safe-key'],undefined);
});
test('Playwright action logs become enums only, preserving intercepted and stability failures without raw DOM',()=>{
  const error=new Error('<header class="op-topbar">private-token</header> intercepts pointer events; element is not stable');error.name='TimeoutError';
  const result=drawerCloseFailure(error);assert.equal(result.timeout,true);assert.equal(result.intercepted,true);assert.equal(result.notStable,true);assert.equal(result.knownInterceptor,'app-topbar');
  assert.doesNotMatch(JSON.stringify(result),/private-token|header|class=|</);
  assert.equal(drawerCloseFailure(new Error('arbitrary private value')).knownInterceptor,'unclassified');
});

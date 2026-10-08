import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { naturalScreenshotPaintReady } from '../screenshot-paint.mjs';
function fixture() {
  const animations=[];
  const node={parentElement:null,style:{opacity:'1',visibility:'visible'},getClientRects:()=>[{}],getBoundingClientRect:()=>({x:0,y:0,width:520,height:900})};
  const context=vm.createContext({window:{},document:{getAnimations:()=>animations,querySelectorAll:()=>[node]},getComputedStyle:value=>value.style});
  return {animations,node,read:()=>vm.runInContext(`(${naturalScreenshotPaintReady.toString()})()`,context)};
}
function animation(state='running',end=300,pending=false) {
  return {playState:state,pending,effect:{getComputedTiming:()=>({endTime:end})},
    finish(){throw new Error('must never finish application motion');},cancel(){throw new Error('must never cancel application motion');}};
}
test('paused rc-motion start and running finite animations must settle naturally before three stable frames',()=>{
  const f=fixture();const motion=animation('paused');f.animations.push(motion);
  for(let i=0;i<5;i++)assert.equal(f.read(),false);
  motion.playState='running';for(let i=0;i<5;i++)assert.equal(f.read(),false);
  motion.playState='finished';assert.equal(f.read(),false);assert.equal(f.read(),false);assert.equal(f.read(),true);
});
test('a newly starting motion resets stable-frame credit and pending finite animations cannot pass',()=>{
  const f=fixture();assert.equal(f.read(),false);assert.equal(f.read(),false);
  const motion=animation('idle',300,true);f.animations.push(motion);assert.equal(f.read(),false);
  motion.pending=false;assert.equal(f.read(),false);assert.equal(f.read(),false);assert.equal(f.read(),true);
});
test('infinite CSS animations stay untouched while translucent dialog ancestry and changing geometry remain blocked',()=>{
  const f=fixture();f.animations.push(animation('running',Infinity));
  f.node.style.opacity='0.5';for(let i=0;i<5;i++)assert.equal(f.read(),false);
  f.node.style.opacity='1';assert.equal(f.read(),false);assert.equal(f.read(),false);
  f.node.getBoundingClientRect=()=>({x:50,y:0,width:520,height:900});assert.equal(f.read(),false);assert.equal(f.read(),false);assert.equal(f.read(),true);
});

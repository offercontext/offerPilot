'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');const {createInstallSafety}=require('../update-safety.cjs');const {CHANNEL}=require('../updater.cjs');
function fixture(snapshot, options={}) {
  const ipcMain=new EventEmitter();const contents=new EventEmitter();contents.mainFrame={url:'http://127.0.0.1:1234/'};contents.isDestroyed=()=>false;
  let requests=0, prompts=0, locked=false;
  contents.send=(_name,payload)=>{requests++; if(options.noReply)return;queueMicrotask(()=>ipcMain.emit(CHANNEL+'prepare-reply',{sender:contents,senderFrame:contents.mainFrame},{id:payload.id,snapshot:typeof snapshot==='function'?snapshot(requests):snapshot}));};
  const window={webContents:contents,isDestroyed:()=>false};
  const safety=createInstallSafety({ipcMain,window,origin:'http://127.0.0.1:1234',timeoutMs:10,dialog:{showMessageBox:async(_w,opts)=>{prompts++;assert.equal(opts.defaultId,0);assert.match(opts.detail,/无法完整自动检测/);options.onPrompt?.(contents);return {response:options.cancel?0:1};}},lock:()=>{locked=true;},unlock:()=>{locked=false;}});
  return {safety,ipcMain,contents,requests:()=>requests,prompts:()=>prompts,locked:()=>locked};
}
const safe={ready:true,hasDraft:false,activeRun:false,pendingApproval:false};
test('known dirty, run, pending and unreadiness hard block before prompt',async()=>{
  for(const patch of [{hasDraft:true},{activeRun:true},{pendingApproval:true},{ready:false}]){const f=fixture({...safe,...patch});assert.equal(typeof await f.safety.prepare(),'string');assert.equal(f.prompts(),0);f.safety.dispose();}
});
test('explicit confirmation then locked final nonce recheck, cancel leaves unlocked',async()=>{
  const f=fixture(safe);assert.equal(await f.safety.prepare(),true);assert.equal(f.requests(),2);assert.equal(f.locked(),true);f.safety.dispose();
  const c=fixture(safe,{cancel:true});assert.equal(typeof await c.safety.prepare(),'string');assert.equal(c.requests(),1);assert.equal(c.locked(),false);c.safety.dispose();
});
test('late dirty state after confirm aborts and unlocks',async()=>{const f=fixture(n=>({...safe,hasDraft:n===2}));assert.match(await f.safety.prepare(),/草稿/);assert.equal(f.locked(),false);f.safety.dispose();});
test('navigation during confirmation, malformed, and lost renderer fail closed',async()=>{
  const nav=fixture(safe,{onPrompt:c=>c.emit('did-start-navigation',{},'http://127.0.0.1:1234/',false,true)});assert.match(await nav.safety.prepare(),/变化/);nav.safety.dispose();
  for(const f of [fixture({ready:true}),fixture(safe,{noReply:true})]){assert.match(await f.safety.prepare(),/无法确认/);assert.equal(f.prompts(),0);f.safety.dispose();}
});

'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {createReminderService}=require('../reminder-service.cjs');
test('reminder IPC rejects foreign windows and child frames and removes all handlers on shutdown',t=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'offerpilot-service-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const handlers=new Map(),powerMonitor=new EventEmitter(),frame={url:'http://127.0.0.1:1234/'},contents={isDestroyed:()=>false,mainFrame:frame,send:()=>{}};
 const service=createReminderService({ipcMain:{handle:(k,v)=>handlers.set(k,v),removeHandler:k=>handlers.delete(k)},Notification:{isSupported:()=>true},powerMonitor,window:{webContents:contents,isDestroyed:()=>false},origin:'http://127.0.0.1:1234',token:'synthetic',userData:dir,isQuitting:()=>false,locale:()=> 'en-US'});
 t.after(()=>service.dispose());const state=handlers.get('offerpilot:reminders:state');
 assert.throws(()=>state({sender:contents,senderFrame:{url:frame.url}}));assert.throws(()=>state({sender:{},senderFrame:frame}));
 const owner={sender:contents,senderFrame:frame};assert.equal(state(owner).preferences.enabled,false);
 assert.equal(handlers.get('offerpilot:reminders:consume')(owner),null);assert.throws(()=>handlers.get('offerpilot:reminders:test')(owner));
 service.dispose();assert.equal(handlers.size,0);assert.equal(powerMonitor.eventNames().length,0);
});

'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const {EventEmitter}=require('node:events');
const {createReminderController}=require('../reminder-controller.cjs');const {emptyState}=require('../reminder-store.cjs');
function fixture(){
 let time=100000000, saved=emptyState(), reads=0, failSave=false, body={items:[]}, reader;
 const notices=[],routes=[];
 const store={load:()=>structuredClone(saved),save:s=>{if(failSave)throw Error('disk');saved=structuredClone(s);}};
 const controller=createReminderController({store,now:()=>time,supported:()=>true,readCandidates:async()=>{reads++;return reader?reader():body;},navigate:r=>routes.push(r),createNotification:()=>{const n=new EventEmitter();n.close=()=>n.emit('close');n.show=()=>{assert.ok(Object.keys(saved.history).length);notices.push(n);};return n;}});
 const enable=()=>controller.configure({enabled:true,scopes:['deadline'],paused:false,quietEnabled:false,quietStart:22,quietEnd:8});
 const due=()=>{body={items:[{id:'application_event:1:deadline',reason_code:'deadline',subject_type:'application_event',subject_id:1,application_id:2,event_id:1,offer_id:null,source_revision:'a'.repeat(64),delivery_due_at:time/1000,delivery_expires_at:time/1000+3600}]};};
 return {controller,enable,due,notices,routes,store,get saved(){return saved;},get reads(){return reads;},set reader(v){reader=v;},set time(v){time=v;},set failSave(v){failSave=v;},clear:()=>{body={items:[]};}};
}
test('disabled controller never reads candidates; attempts persist before show and dedupe resume',async()=>{
 const f=fixture();f.due();await f.controller.tick();assert.equal(f.reads,0);f.enable();await f.controller.tick();assert.equal(f.notices.length,1);f.controller.suspend();await f.controller.resume();await f.controller.tick();assert.equal(f.notices.length,1);
});
test('disable invalidates an in-flight read and prevents notification',async()=>{
 const f=fixture();f.enable();let resolve;f.reader=()=>new Promise(r=>{resolve=r;});const pending=f.controller.tick();
 f.controller.configure({...f.controller.status().preferences,enabled:false});resolve({items:[]});await pending;assert.equal(f.notices.length,0);
});
test('concurrent polls share one read, native click revalidates before bounded navigation',async()=>{
 const f=fixture();f.enable();f.due();await Promise.all([f.controller.tick(),f.controller.tick()]);assert.equal(f.notices.length,1);f.notices[0].emit('click');await new Promise(setImmediate);assert.deepEqual(f.routes,[{applicationId:2,eventId:1}]);f.notices[0].emit('click');assert.equal(f.routes.length,1);
});
test('deleted or cancelled source cannot navigate on a previously displayed notification',async()=>{
 const f=fixture();f.enable();f.due();await f.controller.tick();f.clear();f.notices[0].emit('click');await new Promise(setImmediate);assert.equal(f.routes.length,0);
});
test('storage failure fails closed before any show and cannot silently reset opt-in',async()=>{
 const f=fixture();f.enable();f.due();f.failSave=true;await f.controller.tick();assert.equal(f.notices.length,0);assert.equal(f.controller.status().error,'storage_failed');assert.throws(()=>f.enable());
});
test('backward clock blocks dispatch and requires explicit disable and re-enable',async()=>{
 const f=fixture();f.enable();f.due();await f.controller.tick();f.time=90000000;await f.controller.tick();assert.equal(f.controller.status().error,'clock_changed');f.controller.configure({...f.controller.status().preferences,enabled:false});f.enable();assert.equal(f.controller.status().error,null);
});

test('late failure of invalidated notification cannot override disabled or storage failure',async()=>{
 for(const failure of [false,true]){
  const f=fixture();f.enable();f.due();await f.controller.tick();
  if(failure){f.failSave=true;await f.controller.tick();}else f.controller.configure({...f.controller.status().preferences,enabled:false});
  const before=f.controller.status();f.notices[0].emit('failed');assert.deepEqual(f.controller.status(),before);
 }
});

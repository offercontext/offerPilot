'use strict';
// Synthetic EventEmitter callbacks exercise controller fencing, not OS visibility.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {EventEmitter}=require('node:events');
const path=require('node:path');
const root=path.resolve(process.env.REMINDER_PRODUCT_SOURCE||path.join(__dirname,'../..'));
const {createReminderController}=require(path.join(root,'desktop/reminder-controller.cjs'));
const {emptyState}=require(path.join(root,'desktop/reminder-store.cjs'));
const policy=require(path.join(root,'desktop/reminder-policy.cjs'));
const flush=()=>new Promise(setImmediate);
function fixture(t){
 let now=1800000000000,saved=emptyState(),reader;
 const original={id:'application_event:1:deadline',reason_code:'deadline',subject_type:'application_event',subject_id:1,application_id:2,event_id:1,offer_id:null,source_revision:'a'.repeat(64),delivery_due_at:now/1000,delivery_expires_at:now/1000+3600};
 let items=[original];const notices=[],routes=[];
 const c=createReminderController({store:{load:()=>structuredClone(saved),save:s=>{saved=structuredClone(s);}},now:()=>now,supported:()=>true,readCandidates:()=>reader?reader():Promise.resolve({items}),navigate:r=>routes.push(r),createNotification:()=>{const n=new EventEmitter();n.show=()=>notices.push(n);n.close=()=>n.emit('close');return n;}});
 c.configure({...policy.defaults(),enabled:true,scopes:['deadline'],quietEnabled:false});t.after(()=>c.dispose());
 return {c,notices,routes,original,set items(v){items=v;},set reader(v){reader=v;},set now(v){now=v;}};
}
test('source changes after display fence synthetic native click without another polling tick',async t=>{
 for(const change of ['deleted','rescheduled','revision','expired']){
  const f=fixture(t);await f.c.tick();assert.equal(f.notices.length,1);
  if(change==='deleted')f.items=[];
  if(change==='rescheduled')f.items=[{...f.original,delivery_due_at:f.original.delivery_due_at+60}];
  if(change==='revision')f.items=[{...f.original,source_revision:'b'.repeat(64)}];
  if(change==='expired')f.now=f.original.delivery_expires_at*1000;
  f.notices[0].emit('click');await flush();assert.deepEqual(f.routes,[],change);
 }
});
test('revocation during pending click revalidation fences navigation and late errors',async t=>{
 for(const revoke of ['disable','scope','pause','suspend','dispose'])for(const fail of [false,true]){
  const f=fixture(t);await f.c.tick();let resolve,reject;
  f.reader=()=>new Promise((yes,no)=>{resolve=yes;reject=no;});
  f.notices[0].emit('click');assert.equal(typeof resolve,'function');
  const prefs=f.c.status().preferences;
  if(revoke==='disable')f.c.configure({...prefs,enabled:false});
  if(revoke==='scope')f.c.configure({...prefs,scopes:['review']});
  if(revoke==='pause')f.c.configure({...prefs,paused:true});
  if(revoke==='suspend')f.c.suspend();
  if(revoke==='dispose')f.c.dispose();
  const state=f.c.status();if(fail)reject(Error('synthetic late network failure'));else resolve({items:[f.original]});
  await flush();assert.deepEqual(f.routes,[],`${revoke}/${fail}`);assert.deepEqual(f.c.status(),state);
 }
});
test('show, close and failed callbacks cannot navigate and failed attempts never auto-replay',async t=>{
 for(const event of ['show','close','failed']){
  const f=fixture(t);await f.c.tick();f.notices[0].emit(event);await flush();assert.deepEqual(f.routes,[]);
  if(event!=='show'){f.notices[0].emit('click');await flush();assert.deepEqual(f.routes,[]);}
  await f.c.tick();assert.equal(f.notices.length,1,event);
 }
});

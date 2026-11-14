'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const os=require('node:os');const path=require('node:path');
const {createReminderStore,emptyState}=require('../reminder-store.cjs');
function workspace(t){const dir=fs.mkdtempSync(path.join(os.tmpdir(),'offerpilot-reminder-test-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));return dir;}
test('atomic checkpoint preserves dedupe and clock upper bound across new store instance',t=>{
 const dir=workspace(t);const store=createReminderStore(dir);assert.equal(store.load().preferences.enabled,false);
 const s=emptyState();s.preferences.enabled=true;s.preferences.scopes=['deadline'];s.enabledAt=100;s.highWater=1000;s.scopeSince.deadline=100;s.history['application_event:1:deadline:0.1']=1000;
 store.save(s);assert.deepEqual(createReminderStore(dir).load(),s);assert.deepEqual(fs.readdirSync(dir),['desktop-reminders.json']);
});
test('corrupt or oversized state never silently opts in or discards dedupe',t=>{
 const dir=workspace(t),file=path.join(dir,'desktop-reminders.json'),store=createReminderStore(dir);
 for(const content of ['{',JSON.stringify({...emptyState(),history:{arbitrary:1}}),'x'.repeat(262145)]){fs.writeFileSync(file,content);assert.throws(()=>store.load());}
});
test('nonregular destination is refused without touching original',t=>{
 const dir=workspace(t);fs.mkdirSync(path.join(dir,'desktop-reminders.json'));
 assert.throws(()=>createReminderStore(dir).save(emptyState()));assert.ok(fs.statSync(path.join(dir,'desktop-reminders.json')).isDirectory());
 assert.equal(fs.readdirSync(dir).length,1);
});

test('workspace migration revokes opt-in and isolates colliding IDs only once',t=>{
 const dir=workspace(t),a='a'.repeat(64),b='b'.repeat(64),key='application_event:1:deadline:100';
 const old=emptyState();old.preferences={...old.preferences,enabled:true,scopes:['deadline'],paused:true};old.history[key]=100000;old.highWater=100000;
 createReminderStore(dir).save(old);
 const first=createReminderStore(dir,a).load();
 assert.equal(first.preferences.enabled,false);assert.equal(first.preferences.paused,true);assert.deepEqual(first.history,{});
 first.preferences.enabled=true;first.history[key]=100001;first.highWater=100001;
 createReminderStore(dir,a).save(first);
 assert.deepEqual(createReminderStore(dir,a).load(),first);
 const imported=createReminderStore(dir,b).load();
 assert.equal(imported.preferences.enabled,false);assert.deepEqual(imported.history,{});assert.equal(imported.highWater,0);assert.deepEqual(imported.scopeSince,{});
 assert.deepEqual(createReminderStore(dir,b).load(),imported);
});
test('disabled preference cannot be revived; invalid identity and corrupt migration fail closed',t=>{
 const dir=workspace(t),file=path.join(dir,'desktop-reminders.json'),a='a'.repeat(64);
 const disabled=createReminderStore(dir,a).load();
 assert.equal(createReminderStore(dir,'b'.repeat(64)).load().preferences.enabled,false);
 for(const bad of [{...disabled,version:2},{...disabled,workspaceId:'wrong'},{...disabled,history:{wrong:1}}]){
  const bytes=JSON.stringify(bad);fs.writeFileSync(file,bytes);
  assert.throws(()=>createReminderStore(dir,a).load());assert.equal(fs.readFileSync(file,'utf8'),bytes);
 }
});
test('failed identity migration preserves old durable state and blocks initialization',async t=>{
 const dir=workspace(t),a='a'.repeat(64),b='b'.repeat(64),store=createReminderStore(dir,a);
 const prior=store.load();prior.preferences={...prior.preferences,enabled:true,scopes:['deadline']};store.save(prior);
 const file=path.join(dir,'desktop-reminders.json'),bytes=fs.readFileSync(file);
 t.mock.method(fs,'renameSync',()=>{throw new Error('synthetic disk failure');});
 assert.throws(()=>createReminderStore(dir,b).load());assert.deepEqual(fs.readFileSync(file),bytes);
 assert.deepEqual(fs.readdirSync(dir),['desktop-reminders.json']);
 const {createReminderController}=require('../reminder-controller.cjs');let reads=0,notices=0;
 const c=createReminderController({store:createReminderStore(dir,b),readCandidates:async()=>{reads++;return {items:[]};},supported:()=>true,createNotification:()=>{notices++;},navigate:()=>{}});
 await c.tick();assert.equal(c.status().error,'storage_failed');assert.equal(reads,0);assert.equal(notices,0);
 t.mock.restoreAll();assert.equal(createReminderStore(dir,b).load().preferences.enabled,false);
});

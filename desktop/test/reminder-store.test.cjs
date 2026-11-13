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

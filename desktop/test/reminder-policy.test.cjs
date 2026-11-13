'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const p = require('../reminder-policy.cjs');
const now = new Date(2026,10,13,12).getTime();
const options = { ...p.defaults(),enabled:true,scopes:['deadline'],quietEnabled:false };
function raw(id=1,due=now/1000-60) { return {id:`application_event:${id}:deadline`,subject_type:'application_event',subject_id:id,
  reason_code:'deadline',source_revision:'a'.repeat(64),application_id:1,event_id:id,offer_id:null,
  delivery_due_at:due,delivery_expires_at:now/1000+3600}; }
test('default does not subscribe, invalid or arbitrary preferences fail closed',()=>{
 assert.equal(p.defaults().enabled,false);assert.deepEqual(p.defaults().scopes,[]);
 for(const x of [{...options,url:'https://example.com'},{...options,scopes:[]},{...options,scopes:['offer_response_deadline']},{...options,quietStart:24}])assert.throws(()=>p.preferences(x));
 assert.deepEqual(p.preferences(options),options);
});
test('display eligibility alone cannot create notification permission',()=>{
 for(const x of [{...raw(),delivery_due_at:null},{...raw(),delivery_expires_at:null},{...raw(),reason_code:'offer_response_deadline'},{...raw(),reason_code:'event_upcoming'}])assert.equal(p.candidate(x),null);
 assert.equal(p.select([p.candidate(raw())],p.defaults(),{},now,0).length,0);
});
test('bounded catchup excludes expired, future and old candidates, including restart baseline',()=>{
 const old=p.candidate(raw(1,now/1000-901)),future=p.candidate(raw(2,now/1000+1)),due=p.candidate(raw(3));
 assert.deepEqual(p.select([old,future,due],options,{},now,0),[due]);
 assert.deepEqual(p.select([due],options,{},now,now),[]);
 assert.equal(p.eligible({...due,expires:now},options,now,0),false);
 assert.equal(p.eligible({...due,due:now-900000},options,now,0),true);
});
test('resume storms and reconnect cannot replay attempted identities',()=>{
 const c=p.candidate(raw());const history={[c.key]:now};
 for(let i=0;i<10;i++)assert.deepEqual(p.select([c],options,history,now+i,0),[]);
 const edited=p.candidate({...raw(),source_revision:'b'.repeat(64)});
 assert.equal(edited.key,c.key);assert.deepEqual(p.select([edited],options,history,now,0),[]);
 assert.equal(p.select([p.candidate(raw(1,now/1000))],options,history,now,0).length,1);
});
test('per-batch and hourly caps hold for many simultaneous deadlines',()=>{
 const cs=Array.from({length:20},(_,i)=>p.candidate(raw(i+1)));
 assert.equal(p.select(cs,options,{},now,0).length,3);
 const history=Object.fromEntries(Array.from({length:9},(_,i)=>['old'+i,now]));
 assert.equal(p.select(cs,options,history,now,0).length,1);
 history.last=now;assert.equal(p.select(cs,options,history,now,0).length,0);
});
test('quiet hours support overnight, daytime, all-day and explicit pause',()=>{
 const at=h=>new Date(2026,10,13,h).getTime();const q={...options,quietEnabled:true};
 assert.equal(p.quiet(q,at(23)),true);assert.equal(p.quiet(q,at(7)),true);assert.equal(p.quiet(q,at(8)),false);
 assert.equal(p.quiet({...q,quietStart:9,quietEnd:17},at(12)),true);
 assert.equal(p.quiet({...q,quietStart:9,quietEnd:9},at(12)),true);
 assert.equal(p.quiet({...options,paused:true},at(12)),true);
});
test('untrusted candidate cannot choose a URL, foreign subject or invalid timestamp',()=>{
 for(const x of [{...raw(),id:'https://example.com'},{...raw(),subject_id:2},{...raw(),application_id:-1},{...raw(),delivery_due_at:Infinity},{...raw(),source_revision:'x'}])assert.equal(p.candidate(x),null);
 const c=p.candidate({...raw(),url:'https://example.com',company:'private'});assert.equal(c.url,undefined);assert.equal(c.company,undefined);
});
test('contradictory or oversized reads reject instead of partially dispatching',()=>{
 assert.throws(()=>p.snapshot({items:[raw(),raw()]}),/duplicate/);
 assert.throws(()=>p.snapshot({items:Array(2001).fill(raw())}),/invalid/);
 assert.throws(()=>p.snapshot({items:null}),/invalid/);
});
test('dedupe pruning never evicts live entries to allow duplicates',()=>{
 assert.deepEqual(p.prune({expired:now-p.HISTORY_MS-1,current:now},now),{current:now});
 assert.throws(()=>p.prune(Object.fromEntries(Array.from({length:1001},(_,i)=>[String(i),now])),now),/history_full/);
});

test('persisted clock high-water blocks replay after forward cleanup and backward restart',()=>{
 const c=p.candidate(raw());let saved={highWater:now,history:{[c.key]:now}};
 const forward=now+9*86400000;
 saved={highWater:p.advanceClock(saved.highWater,forward),history:p.prune(saved.history,forward)};
 const restarted=JSON.parse(JSON.stringify(saved));
 assert.throws(()=>p.advanceClock(restarted.highWater,now+300000),/clock_changed/);
 // Explicit re-enable starts a fresh eligibility baseline, never old backlog.
 assert.deepEqual(p.select([c],options,restarted.history,now+300000,now+300000),[]);
 assert.equal(p.advanceClock(now,now-1000),now);
});

test('remaining dedupe capacity bounds a batch without erasing live history',()=>{
 const history=Object.fromEntries(Array.from({length:999},(_,i)=>[String(i),now-3600001]));
 const prefs={...p.defaults(),enabled:true,scopes:['deadline'],quietEnabled:false};
 const c={id:'application_event:1:deadline',reason:'deadline',due:now,expires:now+1000,key:'fresh'};
 assert.equal(p.select([c,{...c,id:'second',key:'second'},{...c,id:'third',key:'third'}],prefs,history,now,0).length,1);
 history.fresh=now;assert.equal(p.select([c],prefs,history,now,0).length,0);assert.equal(Object.keys(p.prune(history,now)).length,1000);
});

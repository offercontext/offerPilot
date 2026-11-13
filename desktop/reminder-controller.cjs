'use strict';
const policy = require('./reminder-policy.cjs');
const { emptyState } = require('./reminder-store.cjs');
// No renderer URL, notification text, or token enters this controller.
function createReminderController({store, readCandidates, createNotification, navigate,
  supported, now=Date.now, changed=()=>{}, text=()=>({title:'OfferPilot',body:'You have a reminder. Open OfferPilot to view it.'})}) {
  let state, error=null, disposed=false, suspended=false, generation=0, running=null;
  const visible=new Map();
  try { state=store.load(); } catch { state=emptyState();error='storage_failed'; }
  function status() {return {preferences:policy.preferences(state.preferences),supported:!!supported(),error,
    status:disposed?'stopped':suspended?'suspended':error?'unavailable':!state.preferences.enabled?'disabled':policy.quiet(state.preferences,now())?'paused':'ready'};}
  function publish(){changed(status());}
  function closeAll(){for(const entry of visible.values()){entry.active=false;entry.notification.close();}visible.clear();}
  function save(next){try{store.save(next);state=next;}catch{error='storage_failed';closeAll();throw new Error(error);}}
  function allowed(c,time){return policy.eligible(c,state.preferences,time,Math.max(state.enabledAt,state.scopeSince[c.reason]??Infinity));}
  function valid(epoch){return !disposed&&!suspended&&epoch===generation&&state.preferences.enabled&&!error;}
  function configure(value){
    const p=policy.preferences(value),time=now();
    if(disposed||error==='storage_failed')throw new Error(error||'stopped');
    const enabling=p.enabled&&!state.preferences.enabled;
    const scopeSince={};for(const reason of p.scopes)scopeSince[reason]=enabling||!state.preferences.scopes.includes(reason)?time:state.scopeSince[reason]??time;
    save({...state,preferences:p,scopeSince,enabledAt:enabling?time:state.enabledAt,highWater:enabling?time:state.highWater});
    generation++;closeAll();if(error==='clock_changed'&&!p.enabled)error=null;publish();
    return status();
  }
  async function click(entry){
    if(!entry.active)return;entry.active=false;entry.notification.close();visible.delete(entry.c.id);
    const epoch=generation;
    try{
      if(!valid(epoch))return;
      const current=policy.snapshot(await readCandidates()).find(c=>c.id===entry.c.id);
      if(!valid(epoch)||!current||current.key!==entry.c.key||current.revision!==entry.c.revision||!allowed(current,now()))return;
      // Only native click invokes navigation; consumer must validate its fixed item route.
      navigate({applicationId:current.applicationId,eventId:current.eventId});
    }catch{if(valid(epoch)){error='connection_failed';publish();}}
  }
  async function run(){
    const epoch=generation;
    if(disposed||suspended||!state.preferences.enabled||error==='storage_failed'||error==='clock_changed')return;
    error=null;
    if(!supported()){error='unsupported';closeAll();publish();return;}
    try{
      const time=now();const highWater=policy.advanceClock(state.highWater,time);
      save({...state,highWater,history:policy.prune(state.history,time)});
      if(policy.quiet(state.preferences,time)){closeAll();publish();return;}
      const candidates=policy.snapshot(await readCandidates());
      if(!valid(epoch))return;
      for(const [id,entry] of visible){const current=candidates.find(c=>c.id===id);if(!current||current.key!==entry.c.key||current.revision!==entry.c.revision||!allowed(current,now())){entry.active=false;entry.notification.close();visible.delete(id);}}
      const selected=policy.select(candidates.filter(c=>allowed(c,now())),state.preferences,state.history,now(),state.enabledAt);
      for(const original of selected){
        const current=policy.snapshot(await readCandidates()).find(c=>c.id===original.id);
        if(!valid(epoch))return;
        const time=now();const highWater=policy.advanceClock(state.highWater,time);
        if(!current||current.key!==original.key||current.revision!==original.revision||!allowed(current,time)||Object.hasOwn(state.history,current.key))continue;
        save({...state,highWater,history:{...state.history,[current.key]:time}});
        const notification=createNotification(text()),entry={c:current,notification,active:true};
        visible.set(current.id,entry);
        notification.on('click',()=>{void click(entry);});
        notification.on('close',()=>{entry.active=false;if(visible.get(current.id)===entry)visible.delete(current.id);});
        notification.on('failed',()=>{if(!entry.active||epoch!==generation||disposed||suspended||visible.get(current.id)!==entry)return;entry.active=false;visible.delete(current.id);error='notification_failed';publish();});
        notification.show(); // Attempt recorded; OS acceptance is not proof of delivery.
      }
      publish();
    }catch(e){error=e.message==='clock_changed'?'clock_changed':error==='storage_failed'?error:'connection_or_notification_failed';closeAll();publish();}
  }
  function tick(){if(running)return running;running=run().finally(()=>{running=null;});return running;}
  function suspend(){generation++;suspended=true;closeAll();publish();}
  function resume(){generation++;suspended=false;return tick();}
  function dispose(){generation++;disposed=true;closeAll();}
  return {status,configure,tick,suspend,resume,dispose};
}
module.exports={createReminderController};

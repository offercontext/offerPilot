'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { preferences,defaults,REASONS,HISTORY_LIMIT } = require('./reminder-policy.cjs');
const finite = n => Number.isFinite(n) && n >= 0;
const historyKey = /^(application:[1-9]\d*:stale_application|application_event:[1-9]\d*:(deadline|interview_preparation|review)):[0-9]+(?:\.[0-9]+)?$/;
function emptyState() { return {version:1,preferences:defaults(),enabledAt:0,highWater:0,scopeSince:{},history:{}}; }
function validateState(s) {
  if (!s || s.version !== 1 || !finite(s.enabledAt) || !finite(s.highWater)
    || !s.scopeSince || typeof s.scopeSince !== 'object' || Array.isArray(s.scopeSince)
    || Object.entries(s.scopeSince).some(([k,v]) => !REASONS.includes(k) || !finite(v))
    || !s.history || typeof s.history !== 'object' || Array.isArray(s.history)
    || Object.keys(s.history).length > HISTORY_LIMIT
    || Object.entries(s.history).some(([k,v]) => !historyKey.test(k) || !finite(v))) throw new Error('invalid_store');
  if (s.workspaceId !== undefined && (typeof s.workspaceId !== 'string' || !/^[a-f0-9]{64}$/.test(s.workspaceId)))throw new Error('invalid_store');
  return {...(s.workspaceId === undefined ? {} : {workspaceId:s.workspaceId}),version:1,preferences:preferences(s.preferences),enabledAt:s.enabledAt,highWater:s.highWater,
    scopeSince:{...s.scopeSince},history:{...s.history}};
}
function createReminderStore(userData,workspaceId) {
  const filename=path.join(userData,'desktop-reminders.json');
  function root() { const s=fs.lstatSync(userData);if (!s.isDirectory() || s.isSymbolicLink()) throw new Error('invalid_store'); }
  const store = {
    load() {
      root();let st;
      try { st=fs.lstatSync(filename); } catch(e) { if(e.code==='ENOENT')return reconcile(emptyState());throw e; }
      if (!st.isFile() || st.isSymbolicLink() || st.size>256*1024)throw new Error('invalid_store');
      return reconcile(validateState(JSON.parse(fs.readFileSync(filename,'utf8'))));
    },
    save(value) {
      root();const state=validateState(value);
      try { const st=fs.lstatSync(filename);if (!st.isFile() || st.isSymbolicLink())throw new Error('invalid_store'); }
      catch(e) { if(e.code!=='ENOENT')throw e; }
      const temporary=path.join(userData,`.desktop-reminders.${randomUUID()}.tmp`);
      try {
        const fd=fs.openSync(temporary,'wx',0o600);
        try {fs.writeFileSync(fd,JSON.stringify(state)+'\n');fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
        fs.renameSync(temporary,filename);
      }finally{try{fs.unlinkSync(temporary);}catch(e){if(e.code!=='ENOENT')throw e;}}
    },
  };
  function reconcile(state){
    if(workspaceId === undefined)return state; // Standalone store callers do not dispatch.
    if(typeof workspaceId!=='string'||!/^[a-f0-9]{64}$/.test(workspaceId))throw new Error('invalid_workspace_context');
    if(state.workspaceId===workspaceId)return state;
    // New/imported workspace must not inherit delivery consent or dedupe identities.
    const next={...emptyState(),workspaceId,preferences:{...state.preferences,enabled:false}};
    store.save(next);
    return next;
  }
  return store;
}
module.exports={emptyState,validateState,createReminderStore};

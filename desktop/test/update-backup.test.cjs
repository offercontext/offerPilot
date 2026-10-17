'use strict';
const {test} = require('node:test'); const assert = require('node:assert/strict'); const fs = require('node:fs/promises'); const os = require('node:os'); const path = require('node:path'); const {EventEmitter} = require('node:events');
const {backupForUpdate} = require('../update-backup.cjs'); const {stopBackendForUpdate} = require('../update-install.cjs');
test('offline local backup preserves source and config without browser snapshot claims', async t => {
  const base = await fs.mkdtemp(path.join(os.tmpdir(),'update-backup-')); t.after(()=>fs.rm(base,{recursive:true,force:true}));
  const userData = path.join(base,'OfferPilot Desktop'); await fs.mkdir(path.join(userData,'data'),{recursive:true});
  await fs.writeFile(path.join(userData,'data','config.json'),'fixture only'); await fs.writeFile(path.join(userData,'data','data.db'),'fixture sqlite');
  await fs.writeFile(path.join(userData,'desktop-port.json'),'{"port":2345}');
  await assert.rejects(backupForUpdate({userData,version:'1',backendExited:false}));
  const backup = await backupForUpdate({userData,version:'1',backendExited:true});
  assert.equal(await fs.readFile(path.join(backup,'data','config.json'),'utf8'),'fixture only');
  assert.equal(await fs.readFile(path.join(userData,'data','config.json'),'utf8'),'fixture only');
  assert.equal(JSON.parse(await fs.readFile(path.join(backup,'complete.json'))).browserStorage,'retained-in-place-not-snapshotted');
  if(process.platform!=='win32') assert.equal((await fs.stat(path.join(backup,'data','config.json'))).mode&0o077,0);
});
test('links abort backup with no complete marker', async t=>{
  const base=await fs.mkdtemp(path.join(os.tmpdir(),'update-link-'));t.after(()=>fs.rm(base,{recursive:true,force:true}));
  const userData=path.join(base,'app');await fs.mkdir(path.join(userData,'data'),{recursive:true});
  await fs.symlink(base,path.join(userData,'data','escape'),process.platform==='win32'?'junction':'dir');
  await assert.rejects(backupForUpdate({userData,version:'1',backendExited:true}),/links/);
  const entries=await fs.readdir(`${userData}-update-backups`);for(const entry of entries) await assert.rejects(fs.access(path.join(`${userData}-update-backups`,entry,'complete.json')));
});
test('backend stop timeout rejects without killing; only actual exit succeeds',async()=>{
  const child=new EventEmitter();child.exitCode=null;child.signalCode=null;child.stdin={end(){}};child.kill=()=>assert.fail('must not kill');
  await assert.rejects(stopBackendForUpdate(child,5),/timed out/);
  const stopping=stopBackendForUpdate(child,100);child.exitCode=0;child.emit('exit',0);await stopping;
  assert.equal(child.listenerCount('exit'),0);
});
test('nonzero watchdog exit or signal cannot authorize backup/install',async()=>{
  for(const [code,signal] of [[1,null],[null,'SIGTERM']]){
    const child=new EventEmitter();child.exitCode=null;child.signalCode=null;child.stdin={end(){}};
    const pending=stopBackendForUpdate(child,100);child.exitCode=code;child.signalCode=signal;child.emit('exit',code,signal);await assert.rejects(pending,/cleanly/);
    await assert.rejects(stopBackendForUpdate(child),/cleanly/);
  }
});
test('backup permission setup failure aborts before sensitive data is copied',async t=>{
  const base=await fs.mkdtemp(path.join(os.tmpdir(),'update-permission-'));t.after(()=>fs.rm(base,{recursive:true,force:true}));
  const userData=path.join(base,'app');await fs.mkdir(path.join(userData,'data'),{recursive:true});await fs.writeFile(path.join(userData,'data','config.json'),'fixture');let dest;
  await assert.rejects(backupForUpdate({userData,version:'1',backendExited:true,restrictDestination:async p=>{dest=p;throw Error('permission failed');}}),/permission failed/);
  assert.deepEqual(await fs.readdir(dest),[]);
});

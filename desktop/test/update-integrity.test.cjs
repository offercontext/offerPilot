'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const {strictSignatureVerifier}=require('../update-signature.cjs');
const {verifyDownloadedUpdate}=require('../update-integrity.cjs');const fs=require('node:fs/promises');const path=require('node:path');const os=require('node:os');const {createHash}=require('node:crypto');
test('strict Authenticode fail closed for missing tool, malformed output and wrong publisher',async()=>{
  const subject='CN=Fixture Publisher';const filename="C:\\Users\\fixture's\\update.exe";
  for(const output of [{Status:'Valid',Subject:'CN=Other'}, {Status:'NotSigned',Subject:subject},'bad json']){
    const verify=strictSignatureVerifier(subject,(_exe,_args,_opts,cb)=>cb(null,typeof output==='string'?output:JSON.stringify(output)));
    assert.notEqual(await verify([subject],filename),null);
  }
  const missing=strictSignatureVerifier(subject,(_e,_a,_o,cb)=>cb(Error('ENOENT')));assert.notEqual(await missing([subject],filename),null);
  const good=strictSignatureVerifier(subject,(exe,args,opts,cb)=>{assert.equal(opts.shell,undefined);assert.match(exe,/System32/);assert.ok(Buffer.from(args.at(-1),'base64').toString('utf16le').includes(Buffer.from(filename,'utf8').toString('base64')));cb(null,JSON.stringify({Status:'Valid',Subject:subject}));});
  assert.equal(await good([subject],filename),null);assert.notEqual(await good(['Other'],filename),null);
});
test('cached downloaded file and final install recheck reject checksum or signature corruption',async t=>{
  const dir=await fs.mkdtemp(path.join(os.tmpdir(),'update-integrity-'));t.after(()=>fs.rm(dir,{recursive:true,force:true}));const filename=path.join(dir,'fixture.exe');await fs.writeFile(filename,'verified fixture');
  const info={files:[{url:'fixture.exe',sha512:createHash('sha512').update('verified fixture').digest('base64'),size:16}]};
  const args={paths:[filename],info,verifier:async()=>null,publisher:'CN=Fixture'};
  await verifyDownloadedUpdate(args);await assert.rejects(verifyDownloadedUpdate({...args,verifier:async()=>'NotSigned'}));
  await fs.writeFile(filename,'tampered fixture');await assert.rejects(verifyDownloadedUpdate(args),/checksum/);
});

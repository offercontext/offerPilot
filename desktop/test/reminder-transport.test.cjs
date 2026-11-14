'use strict';
const {test}=require('node:test');const assert=require('node:assert/strict');const http=require('node:http');
const {readReminderContext}=require('../reminder-transport.cjs');
const {TOKEN_HEADER}=require('../lifecycle.cjs');
test('workspace context uses fixed authenticated endpoint and rejects redirects or invalid identity',async t=>{
 let status=200,body={workspace_id:'a'.repeat(64)};const requests=[];
 const server=http.createServer((req,res)=>{requests.push({path:req.url,token:req.headers[TOKEN_HEADER.toLowerCase()]});res.writeHead(status,{'content-type':'application/json',location:'https://untrusted.invalid/'});res.end(JSON.stringify(body));});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));t.after(()=>server.close());
 const origin=`http://127.0.0.1:${server.address().port}`;
 assert.equal(await readReminderContext(origin,'synthetic'),'a'.repeat(64));
 assert.deepEqual(requests,[{path:'/api/reminders/desktop-context',token:'synthetic'}]);
 for(const invalid of [{}, {workspace_id:'secret'}, {workspace_id:4}]){body=invalid;await assert.rejects(readReminderContext(origin,'synthetic'));}
 status=302;await assert.rejects(readReminderContext(origin,'synthetic'));
 await assert.rejects(readReminderContext('https://untrusted.invalid','synthetic'));
 assert.equal(requests.length,5);
});

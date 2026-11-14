'use strict';
const http=require('node:http');
const {TOKEN_HEADER}=require('./lifecycle.cjs');
// Fixed authenticated loopback endpoint. Redirects, renderer URLs and oversized
// responses are rejected; failures never log tokens or response bodies.
function readFixed(origin,token,endpoint){
 const url=new URL(origin);
 if(url.protocol!=='http:'||url.hostname!=='127.0.0.1'||url.username||url.password||url.pathname!=='/'||url.search||url.hash)throw new Error('invalid_origin');
 return new Promise((resolve,reject)=>{
  const request=http.get(new URL(endpoint,url),{headers:{[TOKEN_HEADER]:token},timeout:5000},response=>{
   if(response.statusCode!==200){response.resume();reject(new Error('candidate_read_failed'));return;}
   let size=0;const chunks=[];
   response.on('data',chunk=>{size+=chunk.length;if(size>2*1024*1024){response.destroy(new Error('candidate_read_failed'));return;}chunks.push(chunk);});
   response.on('error',()=>reject(new Error('candidate_read_failed')));
   response.on('end',()=>{try{resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{reject(new Error('candidate_read_failed'));}});
  });
  const deadline=setTimeout(()=>request.destroy(new Error('candidate_read_failed')),5000);
  request.on('close',()=>clearTimeout(deadline));
  request.on('timeout',()=>request.destroy(new Error('candidate_read_failed')));
  request.on('error',()=>reject(new Error('candidate_read_failed')));
 });
}
const readReminderCandidates=(origin,token)=>readFixed(origin,token,'/api/reminders/candidates?timezone=UTC');
async function readReminderContext(origin,token){
 const body=await readFixed(origin,token,'/api/reminders/desktop-context');
 if(!body||typeof body.workspace_id!=='string'||!/^[a-f0-9]{64}$/.test(body.workspace_id))throw new Error('invalid_workspace_context');
 return body.workspace_id;
}
module.exports={readReminderCandidates,readReminderContext};

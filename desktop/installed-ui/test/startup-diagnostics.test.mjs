import test from 'node:test';
import assert from 'node:assert/strict';
import { reloadOnceWithObserver } from '../startup-diagnostics.mjs';
function fixture({attached=true,pending=false}={}){
 let reloads=0;let shots=0;let sized=false;
 const page={async reload(options){assert.equal(options.waitUntil,'domcontentloaded');reloads++;},getByRole(role,{name}){assert.equal(role,'navigation');assert.equal(name,'主导航');return {async waitFor(){}};},async waitForFunction(){}};
 const qa={async run(id,name,path,action,kind){assert.equal(id,'STARTUP');assert.equal(kind,'diagnostic');await action();},async size(width,height){assert.equal(width,1280);assert.equal(height,900);sized=true;},async capture(){assert.equal(sized,true,'native size must be established before capture');shots++;},observed(){}};
 const runtime={attached,hasPendingWrite:()=>pending};return{qa,page,runtime,counts:()=>({reloads,shots})};
}
test('startup diagnostic reload happens exactly once after observer attachment',async()=>{
 const f=fixture();await reloadOnceWithObserver(f.qa,f.page,f.runtime);assert.deepEqual(f.counts(),{reloads:1,shots:1});
 await assert.rejects(reloadOnceWithObserver(f.qa,f.page,f.runtime));assert.equal(f.counts().reloads,1);
});
test('missing observer or unresolved write prevents startup reload entirely',async()=>{
 for(const options of [{attached:false},{pending:true}]){const f=fixture(options);await assert.rejects(reloadOnceWithObserver(f.qa,f.page,f.runtime));assert.deepEqual(f.counts(),{reloads:0,shots:0});}
});

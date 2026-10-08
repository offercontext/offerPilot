import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { createCoverage, observeRuntime } from '../coverage-recorder.mjs';
import { ROOT_CASES, SUBVIEWS } from '../coverage-model.mjs';
import { PIN } from '../contract.mjs';
import { markUiStep } from '../ui-locators.mjs';
import { rootSweep } from '../screen-coverage.mjs';

async function fixture(t, { pendingWrite=false, overflow=false, covered=0, wrongHeight=false, columnOverflow=0, clippedControls=0, cardClipped=0, columnCount=6, unownedControls=0, companion=false, mainIdentity=true }={}) {
  const evidence = await fs.mkdtemp(path.join(os.tmpdir(), 'offerpilot-unit-coverage-'));
  t.after(() => fs.rm(evidence,{recursive:true,force:true}));
  let reloads=0; let width=1280; let height=900;
  const sizes=[];
  const page={
    async evaluate(_callback,args) { if(args?.rules)return {observedView:'applications-list',visibleSurfaces:mainIdentity?[args.targetId]:['R05'],targetSurfaceConfirmed:mainIdentity}; return {width,height:wrongHeight?height-1:height,documentWidth:overflow?2000:width,theme:'dark',haruCoveredControls:covered,kanbanColumnCount:columnCount,kanbanColumnHorizontalOverflow:columnOverflow,kanbanControlsOutsideColumn:clippedControls,kanbanControlsOutsideCard:cardClipped,kanbanUnownedControls:unownedControls}; },
    async screenshot({path,animations}) { assert.equal(animations,'allow','capture must not finish or cancel application animations'); await fs.writeFile(path,'UNIT TEST ONLY, NOT A PRODUCT SCREENSHOT'); },
    async reload() { reloads++; },
    async waitForFunction(callback,value,options) { if (value === undefined) { assert.equal(options.timeout,5000); assert.equal(options.polling,'raf'); return; } assert.equal(width,value.width); assert.equal(height,value.height); },
  };
  const app={async browserWindow() { return { async evaluate(callback,value) { callback({setContentSize(w,h){ sizes.push([w,h]); width=w; height=h; }},value); } }; }};
  let calls = 0; let critical = 0; const classifications = {};
  const runtime={snapshot:()=>({classifications:{...classifications},ownRequestFailures:[],ownCriticalFailureCount:critical}),hasPendingWrite:()=>pendingWrite && ++calls > 1};
  const haru = companion ? { ...page, async evaluate(_callback,args) { return args?.rules ? {observedView:'unknown',visibleSurfaces:['S24','S25'],targetSurfaceConfirmed:true} : {width:260,height:340,documentWidth:260,theme:'dark',haruCoveredControls:0}; } } : undefined;
  const qa=await createCoverage({app,page,haru,evidence,pin:PIN,installedExeSha256:'unit-fixture-hash',runtime,setStage(){}});
  return {qa,haru,evidence,sizes,reloads:()=>reloads, runtimeError:(name)=>{ classifications[name]=(classifications[name]||0)+1; }, criticalFailure:()=>{critical++;}};
}

test('recorder writes pin, native measured size, explicit assertion and screenshot before passing',async(t)=>{
  const {qa,evidence,sizes}=await fixture(t);
  qa.fixture('application',27);
  await qa.size(900);
  await qa.run('R05','list-visible',['投递','列表'],async()=>{
    qa.observed('visible real record ID verified'); await qa.capture('list-visible');
  });
  const report=JSON.parse(await fs.readFile(path.join(evidence,'coverage.json'),'utf8'));
  assert.equal(report.fullRegression,PIN.fullRegressionRunId === null ? 'not-run-package-only' : 'independent-not-certified');
  assert.equal(report.sourceCommit,PIN.commit); assert.equal(report.installerSha256,PIN.installerSha256);
  assert.deepEqual(sizes,[[900,900]]); assert.equal(report.cases[0].outcome,'PASS');
  assert.deepEqual(report.screens[0].fixtureIds,[{kind:'application',id:27}]);
  assert.equal(report.screens[0].width,900);
  assert.equal(report.summary.functionalPasses,1);
  assert.match(report.screenshotAnimationPolicy,/settles naturally/);
  assert.match(report.screenshotAnimationPolicy,/no animation finishing\/cancellation/);
});

test('recorder uses actual native 689px content height for screenshot regression',async(t)=>{
  const {qa,evidence,sizes}=await fixture(t);
  await qa.size(1008,689);
  await qa.run('R05','list-1008x689',['投递','列表'],async()=>{
    qa.observed('native viewport verified'); await qa.capture('list-1008x689');
  });
  const report=JSON.parse(await fs.readFile(path.join(evidence,'coverage.json'),'utf8'));
  assert.deepEqual(sizes,[[1008,689]]);
  assert.equal(report.screens[0].width,1008); assert.equal(report.screens[0].height,689);
});

test('a changed native height cannot pass screenshot geometry',async(t)=>{
  const {qa,evidence}=await fixture(t,{wrongHeight:true});
  await qa.size(1008,689);
  await qa.run('R05','wrong-height',['投递','列表'],async()=>{
    qa.observed('window sized'); await qa.capture('wrong-height');
  });
  const report=JSON.parse(await fs.readFile(path.join(evidence,'coverage.json'),'utf8'));
  assert.equal(report.cases[0].outcome,'FAIL');
  assert.ok(report.cases[0].screenshots.length>0);
});

test('geometry failure retains screenshot, remains failed, and independent case still executes',async(t)=>{
  const {qa,evidence,reloads}=await fixture(t,{overflow:true});
  await qa.run('R05','overflow',['投递'],async()=>{qa.observed('selected');await qa.capture('overflow');});
  let continued=false;
  await qa.run('R06','next',['面试'],async()=>{continued=true;throw new Error('synthetic failure');});
  assert.equal(continued,true); assert.equal(reloads(),2);
  const report=JSON.parse(await fs.readFile(path.join(evidence,'coverage.json'),'utf8'));
  assert.equal(report.summary.counts.FAIL,2);
  assert.ok(report.screens.length>=2);
  assert.equal(report.cases[0].screenshots[0],'screens/001-overflow.png');
});

test('Haru hit interception is a failure, even if document has no overflow',async(t)=>{
  const {qa}=await fixture(t,{covered:1});
  await qa.run('S25','hitbox',['Haru'],async()=>{qa.observed('visible');await qa.capture('hitbox');});
  assert.equal(qa.report.cases[0].outcome,'FAIL');
});

test('failure never reloads an unresolved UI write and never serializes sensitive error content',async(t)=>{
  const {qa,evidence,reloads}=await fixture(t,{pendingWrite:true});
  await qa.run('S02','uncertain-save',['添加投递'],async()=>{throw new Error('token=secret ws://127.0.0.1:9333/private');});
  assert.equal(reloads(),0);
  assert.equal(qa.report.cases[0].recovery,'blocked-pending-write');
  const text=await fs.readFile(path.join(evidence,'coverage.json'),'utf8');
  assert.doesNotMatch(text,/token=secret|ws:\/\/|private/);
});

test('finish enumerates unvisited roots and subviews as NOT RUN, never manufactured passes',async(t)=>{
  const {qa}=await fixture(t);
  await qa.finish();
  assert.equal(qa.report.cases.length,ROOT_CASES.length + SUBVIEWS.length);
  assert.equal(qa.report.summary.counts['NOT RUN'],ROOT_CASES.length + SUBVIEWS.length);
  assert.equal(qa.report.summary.status,'incomplete');
  assert.equal(qa.report.cases.find(item => item.surfaceId === 'S32')?.outcome, 'NOT RUN');
  await qa.finish(); assert.equal(qa.report.cases.length,ROOT_CASES.length + SUBVIEWS.length);
});

test('observer publishes only bounded classifications and marks pending mutations until settled',()=>{
  const page=new EventEmitter();page.url=()=> 'http://127.0.0.1:8000/?token=never-retain';
  const runtime=observeRuntime(page);
  page.emit('pageerror',new Error('CSP unsafe-eval sk-secret'));
  page.emit('console',{type:()=> 'error',text:()=> 'Haru Live2D failure ws://private'});
  const request={method:()=> 'POST',url:()=> 'http://127.0.0.1:8000/api/anything?token=private'};
  page.emit('request',request);assert.equal(runtime.hasPendingWrite(),true);
  page.emit('requestfinished',request);assert.equal(runtime.hasPendingWrite(),false);
  for(let i=0;i<120;i++)page.emit('response',{status:()=>500,url:request.url});
  const report=runtime.snapshot();
  assert.equal(report.ownRequestFailures.length,100);assert.equal(report.bounded,true);
  assert.deepEqual(report.classifications,{'csp-runtime-block':1,'haru-runtime-error':1});
  assert.doesNotMatch(JSON.stringify(report),/sk-secret|token|ws:|private/);
});

test('critical request failure cannot disappear after the bounded sample is full',()=>{
  const page=new EventEmitter();page.url=()=> 'http://127.0.0.1:8000';
  const runtime=observeRuntime(page);
  for(let i=0;i<100;i++)page.emit('response',{status:()=>404,url:()=> 'http://127.0.0.1:8000/api/applications/17/material-kit'});
  assert.equal(runtime.snapshot().ownCriticalFailureCount,0);
  page.emit('response',{status:()=>500,url:()=> 'http://127.0.0.1:8000/api/required'});
  assert.equal(runtime.snapshot().ownCriticalFailureCount,1);
  assert.equal(runtime.snapshot().ownRequestFailures.length,100);
  assert.equal(runtime.snapshot().requestFailureCount,101);
  assert.equal(runtime.snapshot().bounded,true);
});

test('ordinary aborted queries are distinguished from failed own-origin transport',()=>{
  const page=new EventEmitter();page.url=()=> 'http://127.0.0.1:8000';
  const runtime=observeRuntime(page);
  const request={method:()=> 'GET',url:()=> 'http://127.0.0.1:8000/api/required'};
  page.emit('requestfailed',{...request,failure:()=>({errorText:'net::ERR_ABORTED'})});
  assert.equal(runtime.snapshot().ownCriticalFailureCount,0);
  page.emit('requestfailed',{...request,failure:()=>({errorText:'net::ERR_CONNECTION_REFUSED private-sensitive-detail'})});
  assert.equal(runtime.snapshot().ownCriticalFailureCount,1);
  assert.doesNotMatch(JSON.stringify(runtime.snapshot()),/private-sensitive|CONNECTION_REFUSED/);
});

test('new scenario cannot navigate or mutate while a previous UI write is unresolved',async(t)=>{
  const {qa}=await fixture(t,{pendingWrite:true});
  await qa.run('S02','save-uncertain',['添加投递'],async()=>{throw new Error('request remains pending');});
  let executed=false;
  await qa.run('S20','next-mutation',['Offer'],async()=>{executed=true;});
  assert.equal(executed,false);
  assert.equal(qa.report.cases.at(-1).outcome,'BLOCKED');
  assert.equal(qa.report.cases.at(-1).reason,'previous-ui-write-still-pending');
});

test('failed POST keeps a sticky unknown-outcome barrier after transport settlement',()=>{
  const page=new EventEmitter();page.url=()=> 'http://127.0.0.1:8000';
  const runtime=observeRuntime(page);
  const request={method:()=> 'POST',url:()=> 'http://127.0.0.1:8000/api/applications',failure:()=>({errorText:'net::ERR_ABORTED'})};
  page.emit('request',request);page.emit('requestfailed',request);
  assert.equal(runtime.hasPendingWrite(),true);
  assert.equal(runtime.snapshot().uncertainMutationOutcome,true);
  page.emit('requestfinished',request);
  assert.equal(runtime.hasPendingWrite(),true,'transport end is not application commit reconciliation');
});

test('only documented read-only missing material kit can be excluded from own-API 4xx failure',()=>{
  const page=new EventEmitter();page.url=()=> 'http://127.0.0.1:8000';
  const runtime=observeRuntime(page);
  const emit=(suffix,status=404,method='GET')=>page.emit('response',{status:()=>status,url:()=>`http://127.0.0.1:8000/api/${suffix}`,request:()=>({method:()=>method})});
  emit('applications/17/material-kit');assert.equal(runtime.snapshot().ownCriticalFailureCount,0);
  emit('applications/17/material-kit',404,'POST');assert.equal(runtime.snapshot().ownCriticalFailureCount,1);
  emit('settings/missing');assert.equal(runtime.snapshot().ownCriticalFailureCount,2);
  emit('applications',422,'POST');assert.equal(runtime.snapshot().ownCriticalFailureCount,3);
});

test('per-screen runtime deltas mark CSP boundary BLOCKED and critical failures FAIL',async(t)=>{
  const csp=await fixture(t);
  await csp.qa.run('S25','csp-runtime',['Haru'],async()=>{csp.runtimeError('csp-runtime-block');csp.qa.observed('fallback is visible');});
  assert.equal(csp.qa.report.cases[0].outcome,'BLOCKED');
  assert.deepEqual(csp.qa.report.cases[0].runtimeDelta,{'csp-runtime-block':1});
  const transport=await fixture(t);
  await transport.qa.run('R01','api-failed',['今日'],async()=>{transport.criticalFailure();transport.qa.observed('root shell renders');});
  assert.equal(transport.qa.report.cases[0].outcome,'FAIL');
  const pageerror=await fixture(t);
  await pageerror.qa.run('R01','page-error',['今日'],async()=>{pageerror.runtimeError('unexpected-page-error');pageerror.qa.observed('root shell renders');});
  assert.equal(pageerror.qa.report.cases[0].outcome,'FAIL');
});

test('a geometry defect stays FAIL while independent normal UI work can finish',async(t)=>{
 const {qa}=await fixture(t,{covered:1});let continued=false;
 await qa.run('S02','visible-defect',['添加投递'],async()=>{
  await qa.capture('before-normal-save');continued=true;qa.observed('ordinary save finished without force');
 });
 assert.equal(continued,true);assert.equal(qa.report.cases[0].outcome,'FAIL');
 assert.equal(qa.report.cases[0].failure.uiIssue,'visual-assertion-failed');
 assert.ok(qa.report.cases[0].visualFailures.some(({issues})=>issues.includes('haru-occlusion')));
});

for (const options of [{columnOverflow:1}, {clippedControls:1}, {cardClipped:1}, {columnCount:0}, {columnCount:5}, {unownedControls:1}]) {
  test(`board-local geometry cannot pass with correct global viewport: ${JSON.stringify(options)}`, async t => {
    const { qa } = await fixture(t, options);
    await qa.run('R04','board-local',['投递','看板'],async()=>{qa.observed('root selected');await qa.capture('board-local');});
    assert.equal(qa.report.cases[0].outcome,'FAIL');
    assert.ok(qa.report.cases[0].visualFailures.length>0);
    assert.equal(qa.report.screens[0].documentWidth,1280);
  });
}
test('Haru failure records actual companion image and exact context step before reload',async t=>{
  const {qa,haru,evidence}=await fixture(t,{companion:true});
  await qa.run('S24','companion-context',['问 Pilot'],async()=>{
    markUiStep(haru,'companion-context','pilot');throw new Error('synthetic wrong context');
  });
  const row=qa.report.cases[0];
  assert.equal(row.outcome,'FAIL');
  assert.deepEqual(row.failedStep,{step:'companion-context',control:'pilot'});
  assert.equal(row.companionScreenshots.length,1);
  const shot=qa.report.companionScreens[0];
  assert.equal(shot.width,260);assert.equal(shot.height,340);assert.equal(shot.confirmsMainSurface,false);
  assert.equal(shot.kind,'companion-diagnostic');
  assert.ok(await fs.readFile(path.join(evidence,shot.filename),'utf8'));
});
test('companion screenshot alone cannot satisfy main target or manufacture functional PASS',async t=>{
  const {qa}=await fixture(t,{companion:true,mainIdentity:false});
  await qa.run('S24','companion-only',['问 Pilot'],async()=>{
    qa.observed('companion only');await qa.captureHaru('companion-only');
    // No thrown test error: only the actual main-target gate may reject this.
  });
  assert.equal(qa.report.cases[0].outcome,'FAIL');
  assert.equal(qa.report.summary.functionalPasses,0);
});

for (const wrapped of [false, true]) {
  test(`strict ORT safe-reload failure (${wrapped ? 'wrapped cleanup' : 'direct guard'}) cannot trigger recorder reload or later UI actions`, async t => {
    const { qa, evidence, reloads } = await fixture(t);
    await qa.run('S30', 'ort-safe-reload-denied', ['设置', '语音'], async () => {
      const denied = new Error('open editing dialog blocks ORT reload');
      throw wrapped ? new AggregateError([denied, new Error('cleanup reload unsafe')], 'ORT probe and cleanup failed') : denied;
    }, 'interaction', { recoveryReload: false });
    assert.equal(reloads(), 0);
    assert.equal(qa.canProceed(), false);
    assert.equal(qa.blockedReason(), 'previous-case-unsafe-ui-recovery');
    let nextAction = false;
    await qa.run('R13', 'later-navigation', ['设置'], async () => { nextAction = true; });
    assert.equal(nextAction, false);
    // No page object is needed: the sweep must stop before any navigation.
    await rootSweep(qa, {}, 'populated-to-supported-extent');
    assert.ok(qa.report.cases.slice(2).every(item => item.outcome === 'BLOCKED'
      && item.reason === 'previous-case-unsafe-ui-recovery'));
    const report = JSON.parse(await fs.readFile(path.join(evidence, 'coverage.json'), 'utf8'));
    assert.equal(report.uiRecoveryBlocked, true);
    assert.equal(report.cases[0].outcome, 'FAIL');
    assert.equal(report.cases[0].recovery, 'disabled-by-case-safety-policy');
    assert.ok(report.cases[0].screenshots.length > 0);
    assert.equal(report.cases[1].outcome, 'BLOCKED');
    assert.equal(report.cases[1].reason, 'previous-case-unsafe-ui-recovery');
  });
}
test('successful strict case does not block later work and ordinary failure still recovers normally', async t => {
  const { qa, reloads } = await fixture(t);
  await qa.run('S30', 'ort-safe-pass', ['设置'], async () => { qa.observed('safe initialized result'); }, 'interaction', { recoveryReload: false });
  assert.equal(qa.report.cases[0].outcome, 'PASS');
  assert.equal(qa.canProceed(), true);
  await qa.run('R13', 'ordinary-failure', ['设置'], async () => { throw new Error('ordinary synthetic UI failure'); });
  assert.equal(reloads(), 1);
  assert.equal(qa.report.cases[1].recovery, 'ordinary-reload-no-write-pending');
  assert.equal(qa.canProceed(), true);
});

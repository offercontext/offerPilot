import test from 'node:test';
import assert from 'node:assert/strict';
import { ROOTS } from '../coverage-model.mjs';
import { bindUiSteps, markUiStep } from '../ui-locators.mjs';
import { navigate, verifyStandaloneHaruContext, recordDesktopMascotScope, closeComparisonSettings, verifyNarrowOfferAction } from '../screen-coverage.mjs';

function navigationPage({ missingIdentity = false, hiddenInterviewBack = false, populatedBoard = false, missingBoardHeader = false } = {}) {
  let view = 'dashboard';
  const actions = [];
  const page = {
    getByRole: (role, options) => locator({ role, ...options }),
    getByText: (name) => locator({ name }),
    getByTestId: (name) => locator({ name }),
    getByPlaceholder: (name) => locator({ name }),
    locator: (selector) => locator({ selector }),
    async waitForURL(predicate) { assert.equal(predicate(new URL(`http://127.0.0.1:8000/?view=${view}`)), true); },
    async waitForFunction() {},
    async evaluate(_callback, args) {
      if (!args?.rules) return;
      const target = ROOTS.find(({ id }) => id === args.targetId);
      return { observedView: view, visibleSurfaces: missingIdentity ? [] : [target.id], targetSurfaceConfirmed: !missingIdentity && target.view === view };
    },
  };
  function locator(value) {
    return {
      page: () => page,
      getByRole: (role, options) => locator({ scope: value, role, ...options }),
      getByPlaceholder: (name) => locator({ scope: value, name }),
      getByText: (name) => locator({ scope: value, name }),
      locator: (selector) => locator({ scope: value, selector }),
      and(other) { return other; },
      or(other) { return other; },
      filter(options) {
        if (options.has) return locator({ ...value, command: '打开 Pilot 工作区' });
        return this;
      },
      async fill(name) { assert.equal(name, '打开 Pilot 工作区'); },
      async count() {
        if (value.name === '待投递') return value.scope?.selector === '[data-kanban-column="pending"] > :first-child' ? (missingBoardHeader ? 0 : 1) : (populatedBoard ? 13 : 1);
        return value.name === '返回上一层' ? 0 : 1;
      },
      async isVisible() {
        if (value.name === '退出沉浸模式，返回原页面') return view === 'pilot';
        if (value.name === '返回面试') return !hiddenInterviewBack;
        return true;
      },
      async waitFor() {
        if (value.name === '待投递' && (missingBoardHeader || (populatedBoard && value.scope?.selector !== '[data-kanban-column="pending"] > :first-child'))) throw new Error('pending marker absent or ambiguous');
        actions.push(['wait', value.name ?? value.selector]);
      },
      async click() {
        if (value.command) { view = 'pilot'; actions.push(['command', value.command]); return; }
        if (value.role === 'button' && value.scope?.name === '主导航') {
          const destination = ROOTS.find(item => item.module === value.name);
          assert.ok(destination, 'only an actual module label may be clicked');
          view = destination.view;
          actions.push(['module', value.name]);
        } else if (value.role === 'tab') {
          assert.equal(value.scope?.selector, '.op-module-tabs', 'root tab must be scoped away from nested tabs');
          view = ROOTS.find(item => item.tab === value.name).view;
          actions.push(['tab', value.name]);
        } else actions.push(['button', value.name]);
      },
      async getAttribute(attribute) {
        if (attribute === 'aria-current') return ROOTS.find(item => item.view === view)?.module === value.name ? 'page' : null;
        if (attribute === 'aria-selected') return ROOTS.find(item => item.view === view)?.tab === value.name ? 'true' : 'false';
        throw new Error('unexpected attribute');
      },
    };
  }
  return { page, actions };
}

for (const root of ROOTS) {
  test(`${root.id} navigation selects its actual module/tab or Pilot command and verifies its landmark`, async () => {
    const { page, actions } = navigationPage();
    await navigate(page, root.view);
    const navigation = actions.filter(([kind]) => ['module', 'tab', 'command'].includes(kind));
    assert.deepEqual(navigation, root.view === 'pilot' ? [['command', '打开 Pilot 工作区']]
      : [['module', root.module], ...(root.tab ? [['tab', root.tab]] : [])]);
  });
}

test('a selected route without its visible root landmark cannot pass', async () => {
  const { page } = navigationPage({ missingIdentity: true });
  await assert.rejects(navigate(page, 'knowledge'), /visible root landmark/);
});

test('unknown roots fail before any UI action, and hidden interview return controls are not clicked', async () => {
  await assert.rejects(navigate({}, 'unknown'), /outside the coverage inventory/);
  const { page, actions } = navigationPage({ hiddenInterviewBack: true });
  await navigate(page, 'interview');
  assert.equal(actions.some(([kind, name]) => kind === 'button' && name === '返回面试'), false);
});

function haruPage({ context = '当前上下文：Synthetic Company · Synthetic Role', failAt } = {}) {
  const steps = [];
  const page = {
    getByRole(role, options) {
      const expected = role === 'main' ? 'Haru 桌面小窗' : 'Haru 对话';
      assert.ok(['main', 'region'].includes(role));
      assert.deepEqual(options, { name: expected, exact: true });
      return {
        async waitFor() {
          steps.push(role);
          if (failAt === role) throw new Error('required standalone surface not visible');
        },
        getByText(value, options) {
          assert.equal(role, 'region');
          assert.deepEqual(options, { exact: true });
          return { async waitFor() {
            steps.push('context');
            if (value !== context) throw new Error('standalone context does not match the clicked row');
          } };
        },
      };
    },
  };
  return { page, steps };
}
const record = { id: 7, company_name: 'Synthetic Company', position_name: 'Synthetic Role' };

test('row context must be read from the actual expanded standalone Haru window', async () => {
  const { page, steps } = haruPage();
  await verifyStandaloneHaruContext(page, record);
  assert.deepEqual(steps, ['main', 'region', 'context']);
});

test('missing, collapsed or stale standalone Haru cannot pass row context coverage', async () => {
  await assert.rejects(verifyStandaloneHaruContext(undefined, record), /Haru window is required/);
  for (const failAt of ['main', 'region']) await assert.rejects(verifyStandaloneHaruContext(haruPage({ failAt }).page, record), /not visible/);
  await assert.rejects(verifyStandaloneHaruContext(haruPage({ context: '当前上下文：工作台' }).page, record), /does not match/);
});

test('removed in-page mascot coverage is explicitly N/A and cannot create a runtime PASS', async () => {
  const rows = [];
  await recordDesktopMascotScope({ disposition: async (...args) => rows.push(args) });
  assert.equal(rows.length, 1);
  assert.equal(rows[0][0], 'S25');
  assert.equal(rows[0][2], 'N/A');
  assert.match(rows[0][3], /standalone runtime.*recorded separately in result.json/);
});

test('populated board with twelve matching card statuses selects only the pending column header', async () => {
  await navigate(navigationPage({populatedBoard:true}).page,'board');
  await assert.rejects(navigate(navigationPage({populatedBoard:true,missingBoardHeader:true}).page,'board'), /absent or ambiguous/);
});
function drawerFixture({ count = 1, blocked = false, failureCapture = false, cleanupFailure = false } = {}) {
  const steps=[];
  const hit={receiver:blocked?'other-element':'target',visible:true,inViewport:true,ancestorPointerDisabled:true};
  const diagnostic={centerReceiver:blocked?'app-topbar':'target',events:[],ancestors:[]};
  const close={count:async()=>count,scrollIntoViewIfNeeded:async()=>steps.push('scroll'),
    evaluate:async fn=>{if(fn.name==='measureControlHit')return hit;assert.equal(fn.name,'installDrawerCloseObservation');steps.push('observe');return diagnostic;},
    click:async options=>{assert.equal(options,undefined);steps.push('click');if(blocked){const error=new Error('<div class="op-topbar">private text</div> intercepts pointer events');error.name='TimeoutError';throw error;}}};
  const settings={getByRole:(role,options)=>{assert.equal(role,'button');assert.ok(options.name.test('关闭'));assert.ok(options.name.test('Close'));assert.equal(options.name.test('关闭全部'),false);return close;},
    waitFor:async options=>{assert.deepEqual(options,{state:'hidden'});steps.push('hidden');}};
  const page={evaluate:async(fn,args)=>{assert.equal(fn.name,'readDrawerCloseObservation');steps.push(args.dispose ? 'dispose' : 'post-capture-observe');if(cleanupFailure && args.dispose)throw new Error('observer cleanup failed');return diagnostic;}};
  const captures=[];
  const qa={capture:async(label,value)=>{captures.push([label,value]);steps.push('screenshot');if(label.endsWith('action-failure')&&failureCapture)throw new Error('screenshot failed');}};
  return {qa,page,settings,steps,captures,hit};
}
test('drawer close preserves one ordinary click and dismissal while collecting bounded before/after evidence',async()=>{
  const f=drawerFixture();await closeComparisonSettings(f.qa,f.page,f.settings);
  assert.deepEqual(f.steps,['scroll','observe','screenshot','post-capture-observe','click','hidden','dispose','screenshot']);
  assert.equal(f.captures[0][1].controlHit,f.hit);
  assert.equal(f.captures[1][0],'offer-comparison-close-action-complete');
});
test('ambiguous or persistently blocked close remains FAIL without fallback and retains safe action diagnostics',async()=>{
  const ambiguous=drawerFixture({count:2});await assert.rejects(closeComparisonSettings(ambiguous.qa,ambiguous.page,ambiguous.settings));assert.deepEqual(ambiguous.steps,[]);
  const blocked=drawerFixture({blocked:true});await assert.rejects(closeComparisonSettings(blocked.qa,blocked.page,blocked.settings),/intercepts pointer/);
  assert.deepEqual(blocked.steps,['scroll','observe','screenshot','post-capture-observe','click','dispose','screenshot']);
  assert.equal(blocked.captures[1][0],'offer-comparison-close-action-failure');
  assert.equal(blocked.captures[1][1].drawerCloseFailure.knownInterceptor,'app-topbar');
  assert.equal(blocked.captures[1][1].drawerCloseFailure.intercepted,true);
  assert.doesNotMatch(JSON.stringify(blocked.captures),/private text|<div/);
});
test('drawer diagnostic failures cannot mask the original click failure or leak an observer after a completed click',async()=>{
  const f=drawerFixture({blocked:true,failureCapture:true});await assert.rejects(closeComparisonSettings(f.qa,f.page,f.settings),/intercepts pointer/);
  assert.ok(f.steps.includes('dispose'));
  const cleanup=drawerFixture({cleanupFailure:true});await assert.rejects(closeComparisonSettings(cleanup.qa,cleanup.page,cleanup.settings),/observer cleanup failed/);
});

function narrowOfferFixture({overflow=false,unscrolled=false,clipped=false,ancestorClipped=false,blocked=false,trialFailure=false,noOverflow=false,cardsClipped=false,initialRight=false,wrongOwner=false,wrongDetail=false,reopenFailure=false,clippedReturnedTitle=false}={}) {
  const steps=[];const sizes=[];const shots=[];const diagnostics=[];
  let scrollLeft=initialRight?95:0;let mode='comparison';
  const localScroll={width:900,documentWidth:overflow?1100:900,controlFullyWithinViewport:!clipped,controlFullyWithinScrollableBounds:!ancestorClipped,
    cardCount:2,allCardsHorizontallyVisible:!cardsClipped,scrollers:[]};
  const hit={receiver:blocked?'other-element':'target'};
  const page={waitForFunction:async()=>{},evaluate:async()=>{},mouse:{wheel:async(dx,dy)=>{assert.equal(dy,0);steps.push(dx>0?'wheel-right':'wheel-left');if(!unscrolled)scrollLeft=dx>0?95:0;}},getByTestId:id=>{assert.equal(id,'offer-comparison-header-7');return scope('target');},
    locator:selector=>{assert.ok(selector.startsWith('[data-core-task-key'));return owner;},
    getByRole:(role,options)=>{
      if(role==='heading'){assert.equal(options.level,3);assert.equal(options.name,'Synthetic Second Offer · Engineer');return{waitFor:async()=>{assert.equal(mode,'detail');if(wrongDetail)throw new Error('wrong returned application detail');steps.push('detail-heading');},
       scrollIntoViewIfNeeded:async()=>steps.push('reveal-detail-heading'),
       evaluate:async fn=>fn.name==='measureControlHit'?{receiver:'target'}:{width:900,documentWidth:900,controlFullyWithinViewport:!clippedReturnedTitle,controlFullyWithinScrollableBounds:!clippedReturnedTitle}};}
      if(role==='tablist'){assert.equal(options.name,'投递详情分段');return{waitFor:async()=>{assert.equal(mode,'detail');steps.push('detail-tabs');}};}
      assert.equal(role,'region');return options.name==='谈薪准备'?form:{waitFor:async()=>{assert.equal(mode,'comparison','comparison is not mounted after canonical owner close');steps.push('comparison');}};
    }};
  const owner={filter(){return this;},count:async()=>1,getAttribute:async name=>{assert.equal(name,'data-core-task-key');return `application.offer_review:applicationId=${wrongOwner?999:12}`;},
    waitFor:async options=>{assert.equal(options.state,'hidden');assert.equal(mode,'detail');steps.push('owner-closed');}};
  function control(kind) {return {page:()=>page,filter(){return this;},or(){return this;},
    scrollIntoViewIfNeeded:async()=>steps.push('scroll'),hover:async()=>steps.push('hover'),
    evaluate:async(fn,args)=>{
      if(fn.name==='measureControlHit')return hit;
      if(fn.name==='waitForHorizontalWheel'){
        assert.equal(args.depth,8);assert.ok(args.direction===1?scrollLeft>args.previous:scrollLeft<args.previous,'real wheel must move');
        return{depth:8,before:args.previous,after:scrollLeft,clientWidth:669,scrollWidth:764,direction:args.direction};
      }
      return{...localScroll,scrollers:noOverflow?[]:[{depth:8,documentScroller:false,scrollLeft,clientWidth:669,scrollWidth:764}]};
    },
    click:async options=>{assert.equal(options?.force,undefined);steps.push(options?.trial?'trial':kind==='target'?'open':'close');if(options?.trial&&trialFailure)throw new Error('actual trial rejected');if(!options?.trial)mode=kind==='target'?'preflight':'detail';}};}
  function scope(kind){return {page:()=>page,getByRole:()=>control(kind)};}
  const form={...scope('close'),getByRole:(role,options)=>{
    if(role==='heading'){assert.equal(options.level,2);assert.equal(options.name,'为 Synthetic Second Offer 准备谈薪');return{waitFor:async()=>steps.push('exact-offer')};}
    return control('close');
  },waitFor:async options=>{assert.equal(options.state,'hidden');assert.equal(mode,'detail');steps.push('closed');}};
  bindUiSteps(page,value=>diagnostics.push(value));
  const qa={size:async(...args)=>{sizes.push(args);markUiStep(page,'viewport-set');},capture:async(name,extra)=>shots.push({name,extra}),canProceed:()=>true,observed:()=>steps.push('observed')};
  const reopenComparison=async()=>{assert.equal(mode,'detail','must verify canonical detail before reopening');steps.push('back-to-board','navigate-offers','reselect-two');if(reopenFailure)throw new Error('comparison reopen failed');steps.push('reopen-comparison');mode='comparison';};
  return{qa,page,steps,sizes,shots,diagnostics,reopenComparison};
}
const secondOffer={id:7,application:{id:12,company_name:'Synthetic Second Offer',position_name:'Engineer'}};
test('second Offer action is reached by genuine local scroll, ordinary pointer, exact preflight, and cancel',async()=>{
 const f=narrowOfferFixture();await verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison);
 assert.deepEqual(f.sizes,[[900,689],[1280]]);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right','trial','open','exact-offer','close','closed','owner-closed','detail-heading','detail-tabs','reveal-detail-heading','back-to-board','navigate-offers','reselect-two','reopen-comparison','comparison','observed']);
 assert.equal(f.shots[0].extra.localScroll.scrollers[0].scrollLeft,95);
 assert.equal(f.shots[0].name,'offer-second-card-action-right-900x689');
});
for(const option of ['overflow','clipped','ancestorClipped','blocked']){
 test(`second Offer ${option} cannot be accepted as locally reachable`,async()=>{
  const f=narrowOfferFixture({[option]:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison));
  assert.deepEqual(f.steps,['scroll','hover','wheel-right']);assert.equal(f.shots.length,2);assert.deepEqual(f.sizes,[[900,689],[1280]]);
 });
}

test('real trial rejection preserves the narrow failure image before any resize',async()=>{
 const f=narrowOfferFixture({trialFailure:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison),/actual trial rejected/);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right','trial']);assert.equal(f.shots.length,2);
 assert.equal(f.shots[1].name,'offer-second-card-action-failure-900x689');assert.deepEqual(f.sizes,[[900,689],[1280]]);
});

test('already-visible second button needs a real wheel after scrollIntoView leaves scrollLeft at zero',async()=>{
 const f=narrowOfferFixture();await verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison);
 assert.deepEqual(f.shots[0].extra.scrollProbe,{mode:'real-horizontal-wheel',movements:[{depth:8,before:0,after:95,clientWidth:669,scrollWidth:764,direction:1}]});
 assert.ok(f.steps.indexOf('wheel-right')>f.steps.indexOf('hover'));
});
test('no horizontal overflow passes only with both full card widths visible, without fabricated wheel evidence',async()=>{
 const f=narrowOfferFixture({noOverflow:true});await verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison);
 assert.deepEqual(f.shots[0].extra.scrollProbe,{mode:'no-horizontal-overflow',movements:[]});
 assert.equal(f.steps.some(value=>value.startsWith('wheel')),false);
 const clipped=narrowOfferFixture({noOverflow:true,cardsClipped:true});await assert.rejects(verifyNarrowOfferAction(clipped.qa,clipped.page,secondOffer,clipped.reopenComparison),/both cards/);
});
test('a no-op real wheel fails and retains the scroll phase after failure capture and viewport restoration',async()=>{
 const f=narrowOfferFixture({unscrolled:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison),/real wheel must move/);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right']);assert.equal(f.shots[0].name,'offer-second-card-action-failure-900x689');
 assert.deepEqual(f.diagnostics.at(-1),{step:'offer-local-scroll',control:'offer'});
});
test('an already right-scrolled pane exercises left then right real wheels rather than accepting a no-op',async()=>{
 const f=narrowOfferFixture({initialRight:true});await verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison);
 assert.deepEqual(f.steps.slice(0,4),['scroll','hover','wheel-left','wheel-right']);
 assert.deepEqual(f.shots[0].extra.scrollProbe.movements.map(value=>[value.before,value.after,value.direction]),[[95,0,-1],[0,95,1]]);
});

test('canonical negotiation close returns to the exact owning detail and comparison must be explicitly reopened',async()=>{
 const f=narrowOfferFixture();await verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison);
 const returned=f.shots.find(shot=>shot.name==='offer-second-preflight-returned-detail-900x689');
 assert.equal(returned.extra.applicationId,12);assert.equal(returned.extra.offerId,7);assert.equal(returned.extra.headingVisibility.controlFullyWithinViewport,true);
 assert.ok(f.steps.indexOf('detail-tabs')<f.steps.indexOf('navigate-offers'));
 assert.ok(f.steps.indexOf('reselect-two')<f.steps.indexOf('comparison'));
});
test('missing real comparison reopen path is rejected before any UI action',async()=>{
 const f=narrowOfferFixture();await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer),/reopen path/);assert.deepEqual(f.steps,[]);assert.deepEqual(f.sizes,[]);
});
for(const option of ['wrongOwner','wrongDetail','clippedReturnedTitle','reopenFailure']){
 test(`canonical ${option} remains a failure rather than assuming comparison persists`,async()=>{
  const f=narrowOfferFixture({[option]:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer,f.reopenComparison));
  assert.equal(f.steps.includes('observed'),false);assert.equal(f.steps.includes('comparison'),false);
  assert.equal(f.shots.at(-1).name,'offer-second-card-action-failure-900x689');
  if(option==='reopenFailure')assert.deepEqual(f.diagnostics.at(-1),{step:'offer-reopen-comparison',control:'offer'});
 });
}

test('Settings exports use each real button for cancel/save, retain per-path screenshot evidence, and require a native probe', async () => {
  const { settingsExportFlows } = await import('../screen-coverage.mjs');
  const f = navigationPage();
  const original = f.page.getByRole;
  const actions = [];
  function exportButton(label) {
    return { page: () => f.page,
      filter({ hasText } = {}) { return hasText ? exportButton(['导出备份', '导出完整数据'].find(value => hasText.test(value))) : this; },
      or(other) { assert.equal(other.label, label); return this; }, label,
      async scrollIntoViewIfNeeded() { actions.push(['scroll', label]); },
      async click(options) { actions.push([options?.trial ? 'trial' : 'click', label]); assert.ok(!options || options.trial === true); },
      async waitFor() {},
    };
  }
  f.page.getByRole = (role, options) => role === 'region' && options.name === '数据与备份'
    ? { page: () => f.page, getByRole: (_role, opts) => exportButton(opts?.name) }
    : original(role, options);
  const cases = []; const screens = []; const observed = [];
  const qa = { run: async (surface, id, uiPath, fn) => { cases.push([surface, id, uiPath]); await fn(); },
    capture: async (label, extra) => screens.push([label, extra]), observed: text => observed.push(text) };
  const probes = [];
  await settingsExportFlows(qa, f.page, async ({ kind, mode, clickButton, setStage }) => {
    probes.push([kind, mode]);
    setStage('settings-export-click');
    await clickButton(kind === 'settings' ? '导出备份' : '导出完整数据');
    return { kind, mode, transientExportRemoved: true };
  });
  assert.deepEqual(probes, [['settings', 'cancel'], ['settings', 'save'], ['workspace', 'cancel'], ['workspace', 'save']]);
  assert.deepEqual(actions.filter(([action]) => action === 'click'), [['click', '导出备份'], ['click', '导出备份'], ['click', '导出完整数据'], ['click', '导出完整数据']]);
  assert.equal(cases.length, 2); assert.equal(screens.length, 6); assert.equal(observed.length, 2);
  assert.equal(screens.filter(([, extra]) => extra?.settingsExport?.transientExportRemoved).length, 4);
  await assert.rejects(settingsExportFlows(qa, f.page), /real installed Settings export probe required/);
});

test('Settings export probe failure cannot be replaced by a toast or a PASS assertion', async () => {
  const { settingsExportFlows } = await import('../screen-coverage.mjs');
  const f = navigationPage();
  const original = f.page.getByRole;
  const button = { page: () => f.page, filter() { return this; }, or() { return this; },
    scrollIntoViewIfNeeded: async () => {}, click: async () => {}, waitFor: async () => {} };
  f.page.getByRole = (role, options) => role === 'region' && options.name === '数据与备份'
    ? { page: () => f.page, getByRole: () => button } : original(role, options);
  let observed = false;
  const qa = { run: async (_surface, _id, _path, fn) => fn(), capture: async () => {}, observed: () => { observed = true; } };
  await assert.rejects(settingsExportFlows(qa, f.page, async () => { throw new Error('native download did not complete'); }), /did not complete/);
  assert.equal(observed, false);
});

test('native clipboard integration scopes the real prepared JD copy button and retains each result capture', async () => {
  const { verifySyntheticClipboard } = await import('../screen-coverage.mjs');
  const actions = []; const screenshots = [];
  const copyButton = { waitFor: async () => actions.push('visible'), scrollIntoViewIfNeeded: async () => actions.push('scroll'),
    click: async options => { assert.deepEqual(options, { trial: true }); actions.push('actionable'); } };
  const page = { getByRole(role, options) {
    assert.equal(role, 'tabpanel'); assert.deepEqual(options, { name: '准备', exact: true });
    return { getByRole(buttonRole, buttonOptions) {
      assert.equal(buttonRole, 'button'); assert.deepEqual(buttonOptions, { name: '复制来源', exact: true }); return copyButton;
    } };
  } };
  const qa = { capture: async (label, evidence) => screenshots.push([label, evidence]) };
  await verifySyntheticClipboard(qa, page, async options => {
    assert.equal(options.copyButton, copyButton);
    assert.equal(options.expectedText, 'https://example.invalid/qa-local-only');
    for (const label of ['clipboard-0-cancel', 'clipboard-1-allow', 'clipboard-2-cancel']) {
      options.setStage(label); await options.capture(label);
    }
    return { preExistingClipboardNeverRead: true };
  });
  assert.deepEqual(actions, ['visible', 'scroll', 'actionable']);
  assert.equal(screenshots.length, 4);
  assert.equal(screenshots.at(-1)[1].clipboard.preExistingClipboardNeverRead, true);
  await assert.rejects(verifySyntheticClipboard(qa, page), /real installed clipboard probe required/);
  await assert.rejects(verifySyntheticClipboard(qa, page, async () => { throw new Error('native copy failed'); }), /native copy failed/);
  for (const captureFails of [false, true]) {
    const steps = []; bindUiSteps(page, value => steps.push(value));
    const failureScreens = [];
    const safe = { schemaVersion: 1, probe: 'clipboard', phase: 'native-arm', outcome: 'failed' };
    const original = new Error('native copy failed without exposing private details');
    await assert.rejects(verifySyntheticClipboard({ capture: async (label, extra) => {
      failureScreens.push([label, extra]); markUiStep(page, 'geometry-check');
      if (captureFails) throw new Error('screenshot also failed');
    } }, page, async ({ onDiagnostic, setStage }) => {
      setStage('clipboard-0-cancel'); await onDiagnostic(safe); throw original;
    }), error => error === original);
    assert.equal(failureScreens.length, 1);
    assert.equal(failureScreens[0][1].clipboardDiagnostic, safe);
    assert.deepEqual(steps.at(-1), { step: 'clipboard-0-cancel', control: 'application-jd' });
    assert.doesNotMatch(JSON.stringify(failureScreens), /private details/);
  }
});

test('offline runtime integration uses non-editing Settings and gates both reloads against pending writes and dialogs', async () => {
  const { offlineOrtFlow } = await import('../screen-coverage.mjs');
  async function run(options = {}) {
    const f = navigationPage(); const original = f.page.getByRole;
    f.page.url = () => `http://127.0.0.1:8000/?view=${options.wrongRoute ? 'board' : 'settings'}`;
    f.page.getByRole = (role, value) => role === 'dialog'
      ? { filter: filter => { assert.deepEqual(filter, { visible: true }); return { count: async () => options.openDialog ? 1 : 0 }; } }
      : original(role, value);
    const locator = f.page.locator;
    f.page.locator = selector => selector === '#voice-settings-title'
      ? { scrollIntoViewIfNeeded: async () => {}, waitFor: async () => {} } : locator(selector);
    const screenshots = []; let observed = false; let reloads = 0;
    const steps = []; bindUiSteps(f.page, value => steps.push(value));
    const qa = { run: async (_id, _case, _path, action, kind, policy) => { assert.equal(kind, 'interaction'); assert.deepEqual(policy, { recoveryReload: false }); return action(); }, capture: async (label, evidence) => { screenshots.push([label, evidence]); markUiStep(f.page, 'geometry-check'); if(options.captureFails && label.endsWith('probe-failure'))throw new Error('screenshot also failed'); },
      canProceed: () => !options.pendingWrite, observed: () => { observed = true; } };
    let caught;
    try { await offlineOrtFlow(qa, f.page, async ({ beforeReload, setStage, onDiagnostic }) => {
      setStage('installed-ort-owner');
      await beforeReload('before-probe'); reloads++;
      setStage('installed-ort-initialize');
      if (options.initializeFails) { await onDiagnostic({ schemaVersion: 1, probe: 'offline-ort', phase: 'renderer-initialization', outcome: 'failed' }); throw new Error('WASM failed'); }
      await beforeReload('release-probe'); reloads++;
      return { mainDocumentProductionCspVerified: true, whisperTranscriptionValidated: false };
    }); } catch(error) { if(!options.expectFailure)throw error; caught=error; }
    return { screenshots, observed, reloads, caught, steps };
  }
  const passed = await run();
  assert.equal(passed.reloads, 2); assert.equal(passed.observed, true); assert.equal(passed.screenshots.length, 2);
  assert.equal(passed.screenshots[1][1].offlineOrt.whisperTranscriptionValidated, false);
  for (const option of ['pendingWrite', 'wrongRoute', 'openDialog', 'initializeFails']) await assert.rejects(run({ [option]: true }));
  for (const captureFails of [false, true]) {
    const failed = await run({ initializeFails: true, expectFailure: true, captureFails });
    assert.equal(failed.caught.message, 'WASM failed'); assert.equal(failed.observed, false);
    assert.equal(failed.screenshots.at(-1)[1].offlineOrtDiagnostic.outcome, 'failed');
    assert.deepEqual(failed.steps.at(-1), { step: 'installed-ort-initialize', control: 'offline-ort' });
  }
});

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
function drawerFixture({ count = 1, blocked = false } = {}) {
  const steps=[];
  const hit={receiver:blocked?'other-dialog-element':'target',visible:true,inViewport:true};
  const close={count:async()=>count,scrollIntoViewIfNeeded:async()=>steps.push('scroll'),evaluate:async()=>hit,
    click:async options=>{assert.equal(options,undefined);steps.push('click');if(blocked)throw new Error('other element intercepts pointer events');}};
  const settings={getByRole:(role,options)=>{assert.equal(role,'button');assert.ok(options.name.test('关闭'));assert.ok(options.name.test('Close'));assert.equal(options.name.test('关闭全部'),false);return close;},
    waitFor:async options=>{assert.deepEqual(options,{state:'hidden'});steps.push('hidden');}};
  const qa={capture:async(label,value)=>{assert.equal(label,'offer-comparison-close-hit');assert.equal(value.controlHit,hit);steps.push('screenshot');}};
  return {qa,settings,steps};
}
test('drawer close uses the exact semantic button, records hit evidence, and requires ordinary click plus dismissal',async()=>{
  const f=drawerFixture();await closeComparisonSettings(f.qa,{},f.settings);
  assert.deepEqual(f.steps,['scroll','screenshot','click','hidden']);
});
test('ambiguous or blocked drawer close cannot fall back to Escape, force, or a false success',async()=>{
  const ambiguous=drawerFixture({count:2});await assert.rejects(closeComparisonSettings(ambiguous.qa,{},ambiguous.settings));assert.deepEqual(ambiguous.steps,[]);
  const blocked=drawerFixture({blocked:true});await assert.rejects(closeComparisonSettings(blocked.qa,{},blocked.settings),/intercepts pointer/);
  assert.deepEqual(blocked.steps,['scroll','screenshot','click']);
});

function narrowOfferFixture({overflow=false,unscrolled=false,clipped=false,ancestorClipped=false,blocked=false,trialFailure=false,noOverflow=false,cardsClipped=false,initialRight=false}={}) {
  const steps=[];const sizes=[];const shots=[];const diagnostics=[];
  let scrollLeft=initialRight?95:0;
  const localScroll={width:900,documentWidth:overflow?1100:900,controlFullyWithinViewport:!clipped,controlFullyWithinScrollableBounds:!ancestorClipped,
    cardCount:2,allCardsHorizontallyVisible:!cardsClipped,scrollers:[]};
  const hit={receiver:blocked?'other-element':'target'};
  const page={mouse:{wheel:async(dx,dy)=>{assert.equal(dy,0);steps.push(dx>0?'wheel-right':'wheel-left');if(!unscrolled)scrollLeft=dx>0?95:0;}},getByTestId:id=>{assert.equal(id,'offer-comparison-header-7');return scope('target');},
    getByRole:(role,options)=>{assert.equal(role,'region');return options.name==='谈薪准备'?form:{waitFor:async()=>steps.push('comparison')};}};
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
    click:async options=>{assert.equal(options?.force,undefined);steps.push(options?.trial?'trial':kind==='target'?'open':'close');if(options?.trial&&trialFailure)throw new Error('actual trial rejected');}};}
  function scope(kind){return {page:()=>page,getByRole:()=>control(kind)};}
  const form={...scope('close'),getByRole:(role,options)=>{
    if(role==='heading'){assert.equal(options.level,2);assert.equal(options.name,'为 Synthetic Second Offer 准备谈薪');return{waitFor:async()=>steps.push('exact-offer')};}
    return control('close');
  },waitFor:async options=>{assert.equal(options.state,'hidden');steps.push('closed');}};
  bindUiSteps(page,value=>diagnostics.push(value));
  const qa={size:async(...args)=>{sizes.push(args);markUiStep(page,'viewport-set');},capture:async(name,extra)=>shots.push({name,extra}),canProceed:()=>true,observed:()=>steps.push('observed')};
  return{qa,page,steps,sizes,shots,diagnostics};
}
const secondOffer={id:7,application:{company_name:'Synthetic Second Offer'}};
test('second Offer action is reached by genuine local scroll, ordinary pointer, exact preflight, and cancel',async()=>{
 const f=narrowOfferFixture();await verifyNarrowOfferAction(f.qa,f.page,secondOffer);
 assert.deepEqual(f.sizes,[[900,689],[1280]]);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right','trial','open','exact-offer','close','closed','comparison','observed']);
 assert.equal(f.shots[0].extra.localScroll.scrollers[0].scrollLeft,95);
 assert.equal(f.shots[0].name,'offer-second-card-action-right-900x689');
});
for(const option of ['overflow','clipped','ancestorClipped','blocked']){
 test(`second Offer ${option} cannot be accepted as locally reachable`,async()=>{
  const f=narrowOfferFixture({[option]:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer));
  assert.deepEqual(f.steps,['scroll','hover','wheel-right']);assert.equal(f.shots.length,2);assert.deepEqual(f.sizes,[[900,689],[1280]]);
 });
}

test('real trial rejection preserves the narrow failure image before any resize',async()=>{
 const f=narrowOfferFixture({trialFailure:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer),/actual trial rejected/);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right','trial']);assert.equal(f.shots.length,2);
 assert.equal(f.shots[1].name,'offer-second-card-action-failure-900x689');assert.deepEqual(f.sizes,[[900,689],[1280]]);
});

test('already-visible second button needs a real wheel after scrollIntoView leaves scrollLeft at zero',async()=>{
 const f=narrowOfferFixture();await verifyNarrowOfferAction(f.qa,f.page,secondOffer);
 assert.deepEqual(f.shots[0].extra.scrollProbe,{mode:'real-horizontal-wheel',movements:[{depth:8,before:0,after:95,clientWidth:669,scrollWidth:764,direction:1}]});
 assert.ok(f.steps.indexOf('wheel-right')>f.steps.indexOf('hover'));
});
test('no horizontal overflow passes only with both full card widths visible, without fabricated wheel evidence',async()=>{
 const f=narrowOfferFixture({noOverflow:true});await verifyNarrowOfferAction(f.qa,f.page,secondOffer);
 assert.deepEqual(f.shots[0].extra.scrollProbe,{mode:'no-horizontal-overflow',movements:[]});
 assert.equal(f.steps.some(value=>value.startsWith('wheel')),false);
 const clipped=narrowOfferFixture({noOverflow:true,cardsClipped:true});await assert.rejects(verifyNarrowOfferAction(clipped.qa,clipped.page,secondOffer),/both cards/);
});
test('a no-op real wheel fails and retains the scroll phase after failure capture and viewport restoration',async()=>{
 const f=narrowOfferFixture({unscrolled:true});await assert.rejects(verifyNarrowOfferAction(f.qa,f.page,secondOffer),/real wheel must move/);
 assert.deepEqual(f.steps,['scroll','hover','wheel-right']);assert.equal(f.shots[0].name,'offer-second-card-action-failure-900x689');
 assert.deepEqual(f.diagnostics.at(-1),{step:'offer-local-scroll',control:'offer'});
});
test('an already right-scrolled pane exercises left then right real wheels rather than accepting a no-op',async()=>{
 const f=narrowOfferFixture({initialRight:true});await verifyNarrowOfferAction(f.qa,f.page,secondOffer);
 assert.deepEqual(f.steps.slice(0,4),['scroll','hover','wheel-left','wheel-right']);
 assert.deepEqual(f.shots[0].extra.scrollProbe.movements.map(value=>[value.before,value.after,value.direction]),[[95,0,-1],[0,95,1]]);
});

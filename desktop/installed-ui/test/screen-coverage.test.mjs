import test from 'node:test';
import assert from 'node:assert/strict';
import { ROOTS } from '../coverage-model.mjs';
import { navigate, verifyStandaloneHaruContext, recordDesktopMascotScope } from '../screen-coverage.mjs';

function navigationPage({ missingIdentity = false, hiddenInterviewBack = false } = {}) {
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
      async count() { return value.name === '返回上一层' ? 0 : 1; },
      async isVisible() {
        if (value.name === '退出沉浸模式，返回原页面') return view === 'pilot';
        if (value.name === '返回面试') return !hiddenInterviewBack;
        return true;
      },
      async waitFor() { actions.push(['wait', value.name ?? value.selector]); },
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

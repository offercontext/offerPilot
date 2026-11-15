import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyApplicationDetail } from '../detail-ui.mjs';
import { SYNTHETIC } from '../contract.mjs';

function fakeDetail({ initialTab = 'preparation', failAt } = {}) {
  let dialogHidden = false;
  let activeTab = initialTab;
  const stages = [];
  const calls = [];
  const page = {
    getByRole(role, options) {
      calls.push({ role, options });
      if (role === 'dialog') {
        assert.deepEqual(options, { name: '添加投递', exact: true });
        return { async waitFor(options) {
          assert.deepEqual(options, { state: 'hidden' });
          if (failAt === 'dialog') throw new Error('dialog remains visible');
          dialogHidden = true;
        } };
      }
      assert.equal(dialogHidden, true, 'no detail locator should run during modal dismissal');
      if (role === 'heading') {
        assert.deepEqual(options, { level: 3, name: `${SYNTHETIC.company_name} · ${SYNTHETIC.position_name}`, exact: true });
        return { async waitFor(options) { assert.deepEqual(options, { state: 'visible' }); } };
      }
      if (role === 'tablist') {
        assert.deepEqual(options, { name: '投递详情分段', exact: true });
        return { getByRole(role, options) {
          assert.equal(role, 'tab');
          assert.deepEqual(options, { name: '概览', exact: true });
          return { async click() {
            if (failAt === 'tab') throw new Error('tab cannot be selected');
            activeTab = 'overview';
          } };
        } };
      }
      if (role === 'tabpanel') {
        assert.deepEqual(options, { name: '概览', exact: true });
        assert.equal(activeTab, 'overview', 'hidden overview cannot satisfy the detail check');
        let panelVisible = false;
        return {
          async waitFor(options) { assert.deepEqual(options, { state: 'visible' }); panelVisible = true; },
          getByText(text, options) {
            assert.equal(panelVisible, true);
            assert.equal(text, SYNTHETIC.notes);
            assert.deepEqual(options, { exact: true });
            return { async waitFor(options) {
              assert.deepEqual(options, { state: 'visible' });
              if (failAt === 'notes') throw new Error('exact notes missing');
            } };
          },
        };
      }
      throw new Error('unexpected detail locator');
    },
    getByText() { throw new Error('notes must be scoped to the visible overview panel'); },
  };
  return { page, stages, calls, setStage: (stage) => stages.push(stage) };
}
for (const initialTab of ['preparation', 'overview']) {
  test(`detail verifier uses supported navigation from ${initialTab}`, async () => {
    const fixture = fakeDetail({ initialTab });
    await verifyApplicationDetail(fixture.page, SYNTHETIC, fixture.setStage);
    assert.deepEqual(fixture.stages, ['form-dismissal', 'heading', 'overview-tab', 'overview-panel', 'notes']);
    assert.deepEqual(fixture.calls.map((call) => call.role), ['dialog', 'heading', 'tablist', 'tabpanel']);
  });
}
for (const [failAt, expectedStage] of [['dialog', 'form-dismissal'], ['tab', 'overview-tab'], ['notes', 'notes']]) {
  test(`detail verifier preserves ${failAt} failure without fallback`, async () => {
    const fixture = fakeDetail({ failAt });
    await assert.rejects(verifyApplicationDetail(fixture.page, SYNTHETIC, fixture.setStage));
    assert.equal(fixture.stages.at(-1), expectedStage);
  });
}

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { waitForInputValue, selectSegment } from '../ui-state.mjs';
import { bindUiSteps } from '../ui-locators.mjs';

function changingInput(values) {
  const page={};const calls=[];let count=0;
  return {calls,input:{page:()=>page,async waitFor(options){calls.push(['wait',options.state]);},
    async inputValue(){calls.push(['read']);return values[Math.min(count++,values.length-1)];},
    async fill(){throw new Error('readback must not overwrite the UI value');}}};
}
test('reopened palette and Ant Form readback wait for effects instead of reading stale retained values',async()=>{
  for(const [values,expected] of [[['old query','old query',''],''],[['unsaved draft','','saved note'],'saved note']]){
    const {input,calls}=changingInput(values);
    assert.equal(await waitForInputValue(input,expected,{pollMs:1,timeoutMs:100}),expected);
    assert.deepEqual(calls[0],['wait','visible']);assert.equal(calls.filter(([name])=>name==='read').length,3);
  }
});
test('a genuinely wrong value remains a bounded failure and cannot expose its contents',async()=>{
  const {input}=changingInput(['private unexpected value']);
  await assert.rejects(waitForInputValue(input,'private expected value',{pollMs:1,timeoutMs:3}),
    (error)=>error.name==='TimeoutError'&&!/private|expected value|unexpected value/.test(error.message));
});
test('segmented helper clicks its real label and verifies the checked radio without force',async()=>{
  const page={};const steps=[];bindUiSteps(page,(step)=>steps.push(step));let clicked=false;let reads=0;
  const radio={async count(){return 1;},async check(){throw new Error('zero-size input cannot be clicked');},
    locator(selector){assert.equal(selector,'xpath=ancestor::label[1]');return {async click(options){assert.equal(options,undefined);clicked=true;}};},
    async isChecked(){assert.equal(clicked,true);return ++reads>=2;}};
  const scope={page:()=>page,getByRole(role,options){assert.equal(role,'radio');assert.deepEqual(options,{name:'复盘重点练习',exact:true});return radio;}};
  await selectSegment(scope,'复盘重点练习',{pollMs:1,timeoutMs:100});
  assert.equal(reads,2);assert.deepEqual(steps.map(({step})=>step),['selection-open','selection-confirm']);
});
test('segmented ambiguity or unchanged selection cannot become pass',async()=>{
  for(const count of [0,2])await assert.rejects(selectSegment({getByRole:()=>({count:async()=>count})},'题库',{timeoutMs:0}));
  await assert.rejects(selectSegment({getByRole:()=>({count:async()=>1,locator:()=>({click:async()=>{}}),isChecked:async()=>false})},'题库',{timeoutMs:0}));
});
test('evidence-backed story role, final history identity and desktop Haru handoff remain explicit',()=>{
  const flow=fs.readFileSync(new URL('../screen-coverage.mjs',import.meta.url),'utf8');
  assert.match(flow,/library\.getByRole\('searchbox', exact\('搜索面试故事'\)\)/);
  assert.match(flow,/qa\.run\('R05', 'native-browser-history-back-forward'/);
  assert.match(flow,/verifyStandaloneHaruContext\(qa\.haru, record\)/);
  assert.match(flow,/btn\(qa\.haru, '打开 OfferPilot 主窗口'\)\.click/);
  assert.match(flow,/command\(page, '打开 Pilot 工作区'\)/);
  assert.doesNotMatch(flow,/dialog\(page, 'Haru 轻量对话'\)|btn\(haru, '展开到 Pilot 工作区'\)/);
  assert.match(flow,/先配置 API key 后即可对话/);
  assert.match(flow,/btn\(page, '发送'\)\.isDisabled/);
  assert.doesNotMatch(flow,/getByRole\('radio',[^\n]+\.check\(/);
});

test('question editor category readback waits for the same open-form effect without replacing the value',async()=>{
  const {input,calls}=changingInput(['','previous category','安装界面验收']);
  const steps=[];bindUiSteps(input.page(),step=>steps.push(step));
  assert.equal(await waitForInputValue(input,'安装界面验收',{control:'question',pollMs:1,timeoutMs:100}),'安装界面验收');
  assert.deepEqual(steps,[{step:'readback',control:'question'}]);
  assert.equal(calls.filter(([name])=>name==='read').length,3);
  const flow=fs.readFileSync(new URL('../screen-coverage.mjs',import.meta.url),'utf8');
  assert.match(flow,/waitForInputValue\(form\.getByLabel\('分类', \{ exact: true \}\), '安装界面验收', \{ control: 'question' \}\)/);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { selectVisibleOption } from '../select-option.mjs';
function fixture({target=11, duplicate=false, absent=false, wrongSelected=false}={}) {
  let active=0; let open=false; let clicks=0; let arrows=0;
  const rendered={
    async count(){return absent?0:active>=target?(duplicate?2:1):0;},
    async click(){assert.equal(open,true);assert.ok(active>=target);clicks++;open=false;},
    and(){return this;},
  };
  const popup={
    async waitFor({state}){assert.equal(open,state==='visible');},async count(){return 1;},
    locator(selector){assert.equal(selector,'.ant-select-item-option');return rendered;},
    getByTitle(){return rendered;},
  };
  const page={locator(selector){assert.equal(selector,'.ant-select-dropdown:not(.ant-select-dropdown-hidden)');return popup;},
    getByRole(){throw new Error('zero-size accessibility mirror must not be clicked');}};
  const input={async click(){throw new Error('transparent input must not be the pointer target');},
    locator(selector){assert.match(selector,/xpath=ancestor/);return {locator(selector){assert.equal(selector,'.ant-select-selector');return {async click(){open=true;}};}};},async press(key){assert.equal(key,'ArrowDown');active++;arrows++;},
    async evaluate(){return wrongSelected?'unexpected':'target label';}};
  return {page,input,counts:()=>({clicks,arrows})};
}
test('Select reaches virtualized late row through real keys and clicks actual visible option',async()=>{
  const {page,input,counts}=fixture();await selectVisibleOption(page,input,'target label');
  assert.deepEqual(counts(),{clicks:1,arrows:11});
});
test('missing, duplicate and mismatched selected labels fail instead of silently choosing another option',async()=>{
  for (const options of [{absent:true},{target:0,duplicate:true},{target:0,wrongSelected:true}]) {
    const {page,input}=fixture(options);await assert.rejects(selectVisibleOption(page,input,'target label',12));
  }
});
test('visible label regex supports a known title plus product lineage without guessing an ID',async()=>{
  const {page,input}=fixture({target:0});await selectVisibleOption(page,input,/^target label$/);
});

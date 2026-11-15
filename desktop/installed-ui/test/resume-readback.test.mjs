import test from 'node:test';
import assert from 'node:assert/strict';
import { verifyReopenedResume } from '../screen-coverage.mjs';
import { bindUiSteps } from '../ui-locators.mjs';

function fixture({ titles=['', '', 'saved synthetic resume'], names=['', 'synthetic candidate'] }={}) {
  const page={};const calls=[];const diagnostics=[];let editorVisible=false;let basicSelected=false;
  bindUiSteps(page,value=>diagnostics.push(value));
  const input=(kind,values)=>{
    let index=0;
    return {page:()=>page,waitFor:async options=>{assert.equal(editorVisible,true);assert.equal(options.state,'visible');if(kind==='name')assert.equal(basicSelected,true);calls.push(`${kind}-visible`);},
      inputValue:async()=>{calls.push(`${kind}-read`);return values[Math.min(index++,values.length-1)];},
      fill:async()=>{throw new Error('readback must not overwrite persisted values');}};
  };
  const titleInput=input('title',titles);const nameInput=input('name',names);
  const button={page:()=>page,filter(){return this;},or(){return this;},click:async options=>{assert.equal(options,undefined);basicSelected=true;calls.push('basic-selected');}};
  const nameLabel={
    filter(options) {
      assert.equal(options.hasText.test('姓名'),true);
      assert.equal(options.hasText.test('其他姓名'),false);
      return { locator(parent) {
        assert.equal(parent,'..');
        return { locator(tag) { assert.equal(tag,'input');return nameInput; } };
      } };
    },
  };
  const editor={page:()=>page,waitFor:async options=>{assert.equal(options.state,'visible');editorVisible=true;calls.push('editor-visible');},
    getByPlaceholder:(value,options)=>{assert.equal(value,'简历标题');assert.deepEqual(options,{exact:true});return titleInput;},
    getByRole:(role,options)=>{assert.equal(role,'navigation');assert.deepEqual(options,{name:'简历章节',exact:true});return{page:()=>page,getByRole:role=>{assert.equal(role,'button');return button;}};},
    locator:selector=>{assert.equal(selector,'label');return nameLabel;}};
  return{editor,calls,diagnostics,titleInput};
}
const options={pollMs:1,timeoutMs:100};

test('reopening a visible resume waits for its effect-populated title and the selected basic-information field',async()=>{
  const f=fixture();
  await verifyReopenedResume(f.editor,'saved synthetic resume','synthetic candidate',options);
  assert.deepEqual(f.calls,['editor-visible','title-visible','title-read','title-read','title-read','basic-selected','name-visible','name-read','name-read']);
  assert.equal(f.diagnostics.filter(value=>value.step==='readback'&&value.control==='resume').length,2);
});
test('the old immediate title assertion reproduces the empty-first-render race',async()=>{
  const f=fixture();
  await assert.rejects(async()=>assert.equal(await f.titleInput.inputValue(),'saved synthetic resume'),{code:'ERR_ASSERTION'});
  // The same locator then settles to the saved value; no reload, fill or retrying
  // a save is needed to pass the corrected read-only verifier.
  await verifyReopenedResume(f.editor,'saved synthetic resume','synthetic candidate',options);
});
for(const field of ['title','name']) {
  test(`a genuinely wrong saved resume ${field} remains a bounded failure without overwriting or exposing it`,async()=>{
    const f=fixture(field==='title'?{titles:['private wrong title']}:{titles:['saved synthetic resume'],names:['private wrong name']});
    await assert.rejects(verifyReopenedResume(f.editor,'saved synthetic resume','synthetic candidate',{pollMs:1,timeoutMs:3}),
      error=>error.name==='TimeoutError'&&!/private|synthetic/.test(error.message));
    assert.deepEqual(f.diagnostics.at(-1),{step:'readback',control:'resume'});
    if(field==='title')assert.equal(f.calls.includes('basic-selected'),false);
  });
}

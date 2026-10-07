import test from 'node:test';
import assert from 'node:assert/strict';
import { button, visibleButtonPattern, bindUiSteps, markUiStep, safeUiFailure } from '../ui-locators.mjs';
function ui(rows) {
  const page = {};
  const matches=(value,expected,exact)=>expected instanceof RegExp?expected.test(value):exact?value===expected:value.includes(expected);
  const locator=(nodes)=>({
    page:()=>page,
    getByRole(role,options={}) {assert.equal(role,'button');return locator(nodes.filter((node)=>options.name===undefined||matches(node.name,options.name,options.exact)));},
    filter({hasText,visible}) {return locator(nodes.filter((node)=>(hasText===undefined||hasText.test(node.text))&&(visible===undefined||node.visible!==false)));},
    or(other) {return locator([...new Set([...nodes,...other.nodes])]);},
    get nodes(){return nodes;},
    async count(){return nodes.length;},
    async click(options){assert.equal(options?.force,undefined);if(nodes.length!==1)throw new Error('strict mode violation');nodes[0].clicked=true;},
  });
  Object.assign(page,locator(rows)); delete page.page;
  return {page,scope:(owner)=>locator(rows.filter((node)=>node.owner===owner))};
}
test('icon names and Ant two-Han typography cannot hide the actual scoped button',async()=>{
  const rows=[{owner:'form',name:'plus 手动添加',text:'手动添加'},{owner:'form',name:'取 消',text:'取 消'},{owner:'form',name:'编辑题目',text:''}];
  const {page}=ui(rows);
  await button(page,'手动添加').click();await button(page,'取消').click();await button(page,'编辑题目').click();
  assert.ok(rows.every((node)=>node.clicked));
});
test('same-label topbar/content buttons remain ambiguous until the intended owner is supplied',async()=>{
  const rows=[{owner:'header',name:'开始面试练习',text:'开始面试练习'},{owner:'content',name:'开始面试练习',text:'开始面试练习'}];
  const {page,scope}=ui(rows);
  assert.equal(await button(page,'开始面试练习').count(),2);
  await assert.rejects(button(page,'开始面试练习').click(),/strict mode/);
  await button(scope('content'),'开始面试练习').click();
  assert.equal(rows[0].clicked,undefined);assert.equal(rows[1].clicked,true);
});
test('exact visible text neither matches destructive longer alternatives nor hidden force-render buttons',async()=>{
  const {page}=ui([{name:'取 消',text:'取 消'},{name:'取消全部',text:'取消全部'},{name:'取消',text:'取消',visible:false}]);
  assert.equal(await button(page,'取消').count(),1);
  for(const label of ['A+B','确认保存（1）']){assert.ok(visibleButtonPattern(label).test(label));assert.equal(visibleButtonPattern(label).test(`${label}其他`),false);}
});
test('diagnostic steps and issues publish only approved enums',async()=>{
  const {page}=ui([{name:'取 消',text:'取 消'}]);const records=[];bindUiSteps(page,(value)=>records.push(value));
  await button(page,'取消').click();assert.deepEqual(records,[{step:'button-action',control:'取消'}]);
  assert.throws(()=>markUiStep(page,'token=secret','取消'));assert.equal(records.length,1);
  assert.equal(safeUiFailure(new Error('strict mode violation private token=secret')),'selector-ambiguous');
  assert.doesNotMatch(JSON.stringify(records),/secret|token/);
});

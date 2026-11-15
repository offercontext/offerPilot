'use strict';

// MOCK ONLY. Every provider byte is synthetic. These fixtures never read a
// credential, user profile, file, URL, or provider response, and never write to
// the application. The real product must validate JSON and handle tool/HITL.
const MODE = 'MOCK';
const MODEL = 'deepseek-flash';
const CASE_IDS = Object.freeze(['connection', 'pilot-stream', 'pilot-hitl-reject',
  'interview-preparation', 'resume-structure', 'offer-negotiation', 'pilot-cancel']);
const SYNTHETIC = Object.freeze({
  company: 'OfferPilot 合成验收公司',
  role: '合成软件测试工程师',
  rawResume: '姓名：合成候选人\n求职意向：软件测试工程师\n技能：JavaScript、Python、自动化测试\n项目：为合成订单服务编写自动化测试，发现并修复三个边界问题。',
  jd: '合成岗位资料。招聘软件测试工程师，负责 JavaScript 和 Python 自动化测试、接口边界分析及团队协作。',
});
const clone = (value) => JSON.parse(JSON.stringify(value));
const fakeUsage = Object.freeze({ prompt_tokens: 20, completion_tokens: 3,
  prompt_cache_hit_tokens: 0, prompt_cache_miss_tokens: 20, total_tokens: 23 });

// src/offerpilot/ai/interview_preparation_proposals.py:
// INTERVIEW_PREPARATION_JSON_SCHEMA / validate_interview_preparation (V1 and V2).
// All five arrays are required; every nonempty item references a literal,
// contiguous excerpt of the single frozen synthetic JD or resume.
const interview = Object.freeze({
  preparation_directions: [{ id: 'mock-direction-1', text: '准备讲解 JavaScript 和 Python 自动化测试的边界用例。',
    evidence_refs: [{ source: 'jd', path: '/jd/text', excerpt: 'JavaScript 和 Python 自动化测试' }] }],
  story_prompts: [{ id: 'mock-story-1', text: '回顾合成订单服务测试中发现边界问题的过程。',
    evidence_refs: [{ source: 'resume', path: '/raw_text', excerpt: '为合成订单服务编写自动化测试，发现并修复三个边界问题。' }] }],
  review_points: [], interviewer_questions: [], items_to_clarify: [],
});
// src/offerpilot/resume_structured_import.py: decode_structured_output.
// Dotted paths have contiguous array indexes; value ⊆ evidence ⊆ rawResume.
const resume = Object.freeze({ fields: [
  { path: 'contact.name', value: '合成候选人', evidence: '姓名：合成候选人' },
  { path: 'career_intent.target_roles.0', value: '软件测试工程师', evidence: '求职意向：软件测试工程师' },
  { path: 'skills.0', value: 'JavaScript', evidence: '技能：JavaScript、Python、自动化测试' },
  { path: 'skills.1', value: 'Python', evidence: '技能：JavaScript、Python、自动化测试' },
  { path: 'skills.2', value: '自动化测试', evidence: '技能：JavaScript、Python、自动化测试' },
] });
// src/offerpilot/ai/offer_negotiation.py: OFFER_NEGOTIATION_JSON_SCHEMA.
// src/offerpilot/ai/offer_negotiation_templates.py: _STATIC_OPTIONS.
// Provider selects only template IDs and evidence IDs. Product renders text.
const offer = Object.freeze({ proposal_status: 'normal',
  communication_goals: [{ id: 'mock-goal-1', template_id: 'goal_focus_request', evidence_ref_ids: ['brief.goal'] }],
  clarification_questions: [{ id: 'mock-question-1', template_id: 'ask_current_compensation_structure',
    evidence_ref_ids: ['offer.base_monthly', 'offer.months_per_year'] }],
  talking_points: [{ id: 'mock-talking-1', template_id: 'say_current_offer_and_request',
    evidence_ref_ids: ['offer.base_monthly', 'offer.months_per_year', 'brief.goal'] }],
  preparation_checks: [{ id: 'mock-check-1', template_id: 'check_goal_and_concern',
    evidence_ref_ids: ['brief.goal', 'brief.concerns'] }],
});
// src/offerpilot/ai/tool_specs/applications.py: create_application provider
// contract has company_name/position_name/status, and confirmation required.
// No notes or unsupported field; no executor/API call occurs in this fixture.
const hitlTool = Object.freeze({ id: 'call_mock_synthetic_application', type: 'function', function: {
  name: 'create_application', arguments: JSON.stringify({ company_name: '合成待拒绝公司',
    position_name: '合成待拒绝岗位', status: 'applied' }),
} });
const segments = Object.freeze([
  '【MOCK 合成回复】\n一、先列出输入范围，检查最小值与最大值。\n',
  '二、在边界两侧各取一个值，观察接口是否保持一致。\n',
  '三、对空字符串、空列表和缺省字段分别设计测试。\n',
  '四、用独立用例检查零值、负值以及超长输入。\n',
  '五、重复发送相同请求，验证结果稳定且没有重复写入。\n',
  '六、测试中途取消，确认任务结束并释放当前连接。\n',
  '七、检查时间先后顺序，覆盖刚开始与即将结束的时刻。\n',
  '八、验证错误提示能够指出问题且不泄露输入内容。\n',
  '九、把修复前后的边界行为记录为可重复的回归用例。\n',
  '十、以上均为离线合成验收文本，不代表真实模型结果。',
]);
function denied() { const error = new Error('MOCK_FIXTURE_DENIED'); error.code = 'MOCK_FIXTURE_DENIED'; throw error; }
const frame = (caseId, delta, finishReason = null, usage = null) => ({
  id: `chatcmpl-mock-${caseId}`, object: 'chat.completion.chunk', created: 1791460800,
  model: MODEL, choices: [{ index: 0, delta, finish_reason: finishReason }], ...(usage ? { usage: clone(usage) } : {}),
});
const event = (value) => `data: ${JSON.stringify(value)}\n\n`;

function fixtureFor(caseId, body) {
  if (!CASE_IDS.includes(caseId) || !body || body.model !== MODEL) denied();
  const streaming = caseId.startsWith('pilot-');
  if ((body.stream === true) !== streaming) denied();
  if (caseId === 'pilot-hitl-reject' && !body.tools?.some((tool) =>
    tool.type === 'function' && tool.function?.name === 'create_application')) denied();
  if (!streaming) {
    const content = caseId === 'connection' ? 'OK (MOCK synthetic connection).'
      : JSON.stringify(caseId === 'interview-preparation' ? interview : caseId === 'resume-structure' ? resume : offer);
    return { mode: MODE, contentType: 'application/json', body: JSON.stringify({
      id: `chatcmpl-mock-${caseId}`, object: 'chat.completion', created: 1791460800, model: MODEL,
      choices: [{ index: 0, finish_reason: 'stop', message: { role: 'assistant', content } }], usage: clone(fakeUsage),
    }) };
  }
  if (caseId === 'pilot-cancel') {
    // Intentionally no finish/usage/[DONE]. Only the product's Stop/disconnect
    // or the real broker deadline may terminate this active request.
    return { mode: MODE, contentType: 'text/event-stream', initialDelayMs: 250, intervalMs: 500,
      frames: [event(frame(caseId, { role: 'assistant', content: '【MOCK 合成取消测试】正在逐段生成。\n' }))],
      repeatFrame: event(frame(caseId, { content: '合成边界检查仍在进行，请通过界面的停止按钮取消。\n' })),
    };
  }
  const deltas = caseId === 'pilot-hitl-reject'
    ? [{ role: 'assistant', content: '' }, { tool_calls: [{ index: 0, ...clone(hitlTool) }] }]
    : segments.map((content, index) => ({ ...(index === 0 ? { role: 'assistant' } : {}), content }));
  return { mode: MODE, contentType: 'text/event-stream', initialDelayMs: 500, intervalMs: 500,
    frames: [...deltas.map((delta) => event(frame(caseId, delta))),
      event(frame(caseId, {}, caseId === 'pilot-hitl-reject' ? 'tool_calls' : 'stop', fakeUsage)), 'data: [DONE]\n\n'],
  };
}
module.exports = { MODE, MODEL, CASE_IDS, SYNTHETIC, fixtureFor };

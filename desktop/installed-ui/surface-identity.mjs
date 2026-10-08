import { ROOTS } from './coverage-model.mjs';

// Read-only production DOM landmarks. These identifiers contain no record titles,
// user-entered values, provider settings, or URL query contents.
export const SURFACE_RULES = Object.freeze([
  ['R01','dashboard','[aria-label="未来 7 天日程"]',[],['添加第一个投递']],
  ['R02','reminders','input[placeholder="搜索流程行动"]'],
  ['R03','calendar','[aria-label="月历"]'], ['R04','board','[aria-label="主要内容"]'],
  ['R05','applications-list','section[aria-label="投递列表"]'],
  ['R06','interview','[data-testid="interview-surface"]'],
  ['R07','questions','section[aria-label="题库模式"], section[aria-label="今日复习模式"]'],
  ['R08','offers','[data-offer-workspace-mode], section[aria-label="Offer 横向对比"]'],
  ['R09','resumes','[aria-label="创建基础简历入口"]'],
  ['R10','reviews','[data-testid="experience-materials-view"]'],
  ['R11','knowledge','input[placeholder="搜索资料内容（中文/英文关键词）"]'],
  ['R12','pilot','[data-testid="pilot-exit-immersive"]'],
  ['R13','settings','#data-backup-settings-title'],
  ['S01',null,'[data-testid="command-palette-results"]'],
  ['S02',null,null,['添加投递']],
  ['S03',null,'[role="tablist"][aria-label="投递详情分段"]'],
  ['S04',null,'[role="menu"]'],
  ['S05',null,null,['投递岗位资料','岗位资料历史']],
  ['S06',null,'[data-testid="schedule-event-form"]'],
  ['S07',null,'section[aria-label="投递准备"]'],
  ['S08',null,'[data-testid="interview-readiness-center"][data-readiness-mode="real"]'],
  ['S09',null,'[data-testid="interview-practice-surface"]'],
  ['S10',null,'[data-interview-studio]'],
  ['S11',null,'section[aria-label="新建面试复盘"], section[aria-label="编辑面试复盘"], section[aria-label="面试复盘建议"]'],
  ['S12',null,'#voice-growth-title'],
  ['S13',null,null,['手动添加题目','编辑题目']],
  ['S14',null,null,['上传简历']], ['S15',null,'section[aria-label="编辑简历"]'],
  ['S16',null,null,['简历版本对比']], ['S17',null,null,['整理面试故事']],
  ['S18',null,null,['上传 Markdown / Text 资料','上传图文资料','粘贴正文']],
  ['S19',null,'.knowledge-source-tabs'],
  ['S20',null,null,['录入 Offer','编辑 Offer','查看 Offer']],
  ['S21',null,'section[aria-label="Offer 横向对比"]'],
  ['S22',null,null,['调整对比项']],
  ['S23',null,'section[aria-label="谈薪准备"]'],
  ['S24',null,'button[aria-label="上下文面板"], main[aria-label="Haru 桌面小窗"] section[aria-label="Haru 对话"]'],
  ['S25',null,'main[aria-label="Haru 桌面小窗"]'],
  ['S26',null,'section[aria-label="AI 设置"]'],
  ['S27',null,null,['确认个人偏好']],
  ['S28',null,'#data-backup-settings-title'], ['S29',null,'#pilot-mascot-settings-title'],
  ['S30',null,'#voice-settings-title'], ['S31',null,'[aria-label="运行日志列表"]'],
].map(([id, view, selector, dialogs=[], buttons=[]])=>Object.freeze({id,view,selector,dialogs,buttons})));

export async function readSurfaceIdentity(page, targetId) {
  return page.evaluate(({rules,knownViews,targetId})=>{
    const visible=(node)=>Boolean(node && node.getClientRects().length && getComputedStyle(node).visibility==='visible');
    const label=(node)=>node.getAttribute('aria-label') || (node.getAttribute('aria-labelledby') || '').split(/\s+/)
      .map((id)=>document.getElementById(id)?.textContent || '').join(' ').replace(/\s+/g,' ').trim();
    const dialogs=[...document.querySelectorAll('[role="dialog"]')].filter(visible).map(label);
    // The companion renderer is not a workspace root, even when its URL has no view.
    const standaloneHaru=[...document.querySelectorAll('main[aria-label="Haru 桌面小窗"]')].some(visible);
    const value=standaloneHaru ? 'desktop-haru' : new URL(location.href).searchParams.get('view') || 'dashboard';
    const observedView=knownViews.includes(value)?value:'unknown';
    const visibleSurfaces=rules.filter((rule)=>{
      if(rule.view && rule.view!==observedView)return false;
      const selectorMatch=Boolean(rule.selector && [...document.querySelectorAll(rule.selector)].some(visible));
      const dialogMatch=rule.dialogs.some((name)=>dialogs.includes(name));
      const buttonMatch=rule.buttons.some((name)=>[...document.querySelectorAll('button')].some((node)=>visible(node)&&node.textContent.trim()===name));
      if(rule.id==='R04')return selectorMatch && [...document.querySelectorAll('span')].some((node)=>visible(node)&&node.textContent.trim()==='待投递');
      return selectorMatch || dialogMatch || buttonMatch;
    }).map(({id})=>id);
    return {observedView,visibleSurfaces,targetSurfaceConfirmed:visibleSurfaces.includes(targetId)};
  },{rules:SURFACE_RULES,knownViews:ROOTS.map(({view})=>view),targetId});
}

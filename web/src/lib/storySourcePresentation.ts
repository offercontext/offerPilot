import { t } from '@/i18n';
import type { InterviewStorySourceCandidates, InterviewStorySourceSelection } from '@/types/interviewStory';

export const STORY_SOURCE_KINDS = { get resume_version() { return t("我的简历"); }, get interview_note() { return t("面试复盘"); }, get mock_turn() { return t("模拟面试"); } } as const;
type Kind = InterviewStorySourceSelection['source_kind'];
export function storySourceLabel(kind: Kind, path: string): string {
  if (kind === 'mock_turn') {
    const match = /^\/turns\/(\d+)\/(question|answer)$/.exec(path);
    return match ? t("第{value}题 · {value2}", { value: Number(match[1]), value2: match[2] === 'question' ? t("面试官提问") : t("我的回答") }) : t("其他内容");
  }
  if (kind === 'interview_note') return ({ '/questions': t("面试问题"), '/self_reflection': t("我的复盘"), '/difficulty_points': t("遇到的难点"), '/mood': t("面试感受"), '/round': t("面试轮次"), '/date': t("面试日期"), '/company': t("公司"), '/position': t("岗位") } as Record<string, string>)[path] ?? t("其他内容");
  const direct: Record<string, string> = { raw_text: t("简历原文"), additional_text: t("补充内容"), 'contact/name': t("姓名"), 'contact/email': t("邮箱"), 'contact/phone': t("联系电话"), 'contact/location': t("所在城市") };
  const relative = path.startsWith('/content_json/') ? path.slice(14) : '';
  if (direct[relative]) return direct[relative];
  const simple = /^(skills|career_intent\/(?:target_roles|target_locations))\/(\d+)$/.exec(relative);
  if (simple) return t("{value} · 第{value2}项", { value: simple[1] === 'skills' ? t("技能") : simple[1].endsWith('target_roles') ? t("求职意向") : t("意向城市"), value2: Number(simple[2]) + 1 });
  const section = /^(education|experience|projects)\/(\d+)\/([a-z_]+)(?:\/(\d+))?$/.exec(relative);
  if (!section) return t("其他内容");
  const names: Record<string, string> = { education: t("教育经历"), experience: t("工作经历"), projects: t("项目经历") };
  const fields: Record<string, string> = { school: t("学校"), degree: t("学历"), major: t("专业"), company: t("公司"), title: t("职位"), name: t("名称"), role: t("承担角色"), start_date: t("开始时间", undefined, "resume.startDate"), end_date: t("结束时间"), highlights: t("经历亮点"), detail: t("经历描述"), description: t("经历描述") };
  return fields[section[3]] ? `${names[section[1]]}${Number(section[2]) + 1} · ${fields[section[3]]}${section[4] ? ` ${Number(section[4]) + 1}` : ''}` : t("其他内容");
}

export interface StoryDisplayLeaf { selection: InterviewStorySourceSelection; label: string; preview: string }
export interface StorySourceGroup { key: string; kind: Kind; title: string; sections: Array<{ key: string; title: string; leaves: StoryDisplayLeaf[] }> }
export function storySourceGroups(candidates: InterviewStorySourceCandidates): StorySourceGroup[] {
  const groups: StorySourceGroup[] = [];
  for (const [kind, items] of [['resume_version', candidates.resumes], ['interview_note', candidates.interview_notes]] as const) {
    items.forEach((item, index) => groups.push({ key: `${kind}:${item.id}`, kind, title: item.label || `${STORY_SOURCE_KINDS[kind]} ${index + 1}`, sections: [{ key: 'content', title: '', leaves: item.leaves.map((leaf) => ({ ...leaf, label: storySourceLabel(kind, leaf.path), selection: { source_kind: kind, source_id: item.id, path: leaf.path } })) }] }));
  }
  const attempts = new Map<number, StorySourceGroup>();
  for (const turn of candidates.mock_turns) {
    let group = attempts.get(turn.attempt_id);
    if (!group) {
      group = { key: `mock_turn:${turn.attempt_id}`, kind: 'mock_turn', title: t("模拟面试记录 {attempt_id}", { attempt_id: turn.attempt_id }), sections: [] };
      attempts.set(turn.attempt_id, group); groups.push(group);
    }
    group.sections.push({ key: String(turn.turn_no), title: t("第{turn_no}题", { turn_no: turn.turn_no }), leaves: turn.leaves.map((leaf) => ({ ...leaf, label: storySourceLabel('mock_turn', leaf.path), selection: { source_kind: 'mock_turn', source_id: turn.attempt_id, path: leaf.path } })) });
  }
  return groups;
}
export function storySelectionKey(selection: InterviewStorySourceSelection): string { return JSON.stringify([selection.source_kind, selection.source_id, selection.path]); }

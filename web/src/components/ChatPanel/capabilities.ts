import { t } from '@/i18n';
import type { ComponentType } from 'react';
import {
  UnorderedListOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  ProfileOutlined,
  CalendarOutlined,
  SearchOutlined,
  DollarOutlined,
  SwapOutlined,
  PlusCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  SaveOutlined,
  MessageOutlined,
  CompassOutlined,
  AimOutlined,
  FlagOutlined,
  SolutionOutlined,
  AudioOutlined,
  AuditOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';

export type ToolKind = 'read' | 'write';

export interface ToolMeta {
  /** Human-readable Chinese label shown in the process timeline. */
  label: string;
  /** Stable authored key for state stored across locale changes. */
  sourceLabel?: string;
  kind: ToolKind;
  icon: ComponentType;
}

/**
 * Metadata for backend agent tools keyed by the
 * tool name the model emits. Used by ProcessTimeline to render each step and
 * by ProposalCard to pick an icon for write confirmations.
 */
export const TOOL_META: Record<string, ToolMeta> = {
  // ---- read ----
  list_applications: { sourceLabel: "查看投递列表", get label() { return t("查看投递列表"); }, kind: 'read', icon: UnorderedListOutlined },
  get_application: { sourceLabel: "查看投递详情", get label() { return t("查看投递详情"); }, kind: 'read', icon: ProfileOutlined },
  list_jd_analyses: { sourceLabel: "查看 JD 分析", get label() { return t("查看 JD 分析"); }, kind: 'read', icon: FileSearchOutlined },
  get_jd_analysis: { sourceLabel: "查看 JD 分析详情", get label() { return t("查看 JD 分析详情"); }, kind: 'read', icon: FileSearchOutlined },
  list_resumes: { sourceLabel: "查看简历", get label() { return t("查看简历"); }, kind: 'read', icon: SolutionOutlined },
  get_resume: { sourceLabel: "读取简历", get label() { return t("读取简历"); }, kind: 'read', icon: SolutionOutlined },
  list_resume_matches: { sourceLabel: "查看简历匹配记录", get label() { return t("查看简历匹配记录"); }, kind: 'read', icon: SolutionOutlined },
  list_notes: { sourceLabel: "查看复盘记录", get label() { return t("查看复盘记录"); }, kind: 'read', icon: FileTextOutlined },
  list_application_events: { sourceLabel: "查看投递事件", get label() { return t("查看投递事件"); }, kind: 'read', icon: CalendarOutlined },
  get_application_event: { sourceLabel: "查看事件详情", get label() { return t("查看事件详情"); }, kind: 'read', icon: CalendarOutlined },
  list_offers: { sourceLabel: "查看 Offer 列表", get label() { return t("查看 Offer 列表"); }, kind: 'read', icon: DollarOutlined },
  get_offer: { sourceLabel: "查看 Offer 详情", get label() { return t("查看 Offer 详情"); }, kind: 'read', icon: DollarOutlined },
  compare_offers: { sourceLabel: "对比 Offer", get label() { return t("对比 Offer"); }, kind: 'read', icon: SwapOutlined },
  // ---- write ----
  save_application_jd_version: { sourceLabel: "保存岗位资料", get label() { return t("保存岗位资料"); }, kind: 'write', icon: SaveOutlined },
  create_application_submission_snapshot: {
    sourceLabel: "冻结投递事实", get label() { return t("冻结投递事实"); },
    kind: 'write',
    icon: SafetyCertificateOutlined,
  },
  record_application_outcome: {
    sourceLabel: "记录投递结果", get label() { return t("记录投递结果"); },
    kind: 'write',
    icon: AuditOutlined,
  },
  create_application: { sourceLabel: "新建投递", get label() { return t("新建投递"); }, kind: 'write', icon: PlusCircleOutlined },
  update_application_status: { sourceLabel: "更新投递状态", get label() { return t("更新投递状态"); }, kind: 'write', icon: EditOutlined },
  add_note: { sourceLabel: "添加复盘记录", get label() { return t("添加复盘记录"); }, kind: 'write', icon: PlusCircleOutlined },
  update_note: { sourceLabel: "更新复盘记录", get label() { return t("更新复盘记录"); }, kind: 'write', icon: EditOutlined },
  delete_note: { sourceLabel: "删除复盘记录", get label() { return t("删除复盘记录"); }, kind: 'write', icon: DeleteOutlined },
  create_application_event: { sourceLabel: "新建投递事件", get label() { return t("新建投递事件"); }, kind: 'write', icon: PlusCircleOutlined },
  update_application_event: { sourceLabel: "更新投递事件", get label() { return t("更新投递事件"); }, kind: 'write', icon: EditOutlined },
  delete_application_event: { sourceLabel: "删除投递事件", get label() { return t("删除投递事件"); }, kind: 'write', icon: DeleteOutlined },
  create_offer: { sourceLabel: "新建 Offer", get label() { return t("新建 Offer"); }, kind: 'write', icon: PlusCircleOutlined },
  update_offer: { sourceLabel: "更新 Offer", get label() { return t("更新 Offer"); }, kind: 'write', icon: EditOutlined },
  save_offer_assessment: { sourceLabel: "保存 Offer 评估", get label() { return t("保存 Offer 评估"); }, kind: 'write', icon: SaveOutlined },
  resume_update_career_intent: { sourceLabel: "更新简历求职意向", get label() { return t("更新简历求职意向"); }, kind: 'write', icon: AimOutlined },
  resume_rewrite_highlight: { sourceLabel: "改写简历亮点", get label() { return t("改写简历亮点"); }, kind: 'write', icon: EditOutlined },
};

export function toolMeta(name: string): ToolMeta {
  return TOOL_META[name] ?? { label: name, kind: 'read', icon: SearchOutlined };
}

export interface Capability {
  id: string;
  group: string;
  label: string;
  hint: string;
  /** Prompt sent (or inserted) when the capability is triggered. */
  prompt: string;
  icon: ComponentType;
}

/** Capabilities offered in the default general assistant mode. */
export const GENERAL_CAPABILITIES: Capability[] = [
  {
    id: 'list-apps',
    get group() { return t("投递"); },
    get label() { return t("查看投递进度"); },
    get hint() { return t("列出当前所有投递及状态"); },
    prompt: '我现在有哪些投递记录？请按状态分组列出。',
    icon: UnorderedListOutlined,
  },
  {
    id: 'weak-points',
    get group() { return t("复盘"); },
    get label() { return t("汇总复盘薄弱点"); },
    get hint() { return t("总结最近面试复盘里反复出现的短板"); },
    prompt: '总结我最近复盘记录里反复出现的薄弱点，并给出改进建议。',
    icon: CompassOutlined,
  },
  {
    id: 'log-review',
    get group() { return t("复盘"); },
    get label() { return t("记录一次面试复盘"); },
    get hint() { return t("把刚结束的面试整理成复盘"); },
    prompt: '帮我记录刚才的面试复盘，我先口述，你帮我结构化整理。',
    icon: EditOutlined,
  },
  {
    id: 'upcoming',
    get group() { return t("日程"); },
    get label() { return t("近期日程"); },
    get hint() { return t("查看最近的笔试、面试、Offer 进展和截止事项"); },
    prompt: '帮我看看最近有哪些笔试、面试、Offer 进展和截止事项。',
    icon: CalendarOutlined,
  },
];

/** Capabilities offered when the thread is bound to an offer (nego coach). */
export const NEGO_CAPABILITIES: Capability[] = [
  {
    id: 'assess',
    group: 'Offer',
    get label() { return t("评估这份 Offer"); },
    get hint() { return t("判断值不值得接受"); },
    prompt: '帮我分析这个 offer 值不值得接受，从薪资、成长、风险综合评估。',
    icon: AimOutlined,
  },
  {
    id: 'simulate',
    get group() { return t("谈薪"); },
    get label() { return t("模拟 HR 压价"); },
    get hint() { return t("HR 说预算有限时怎么回应"); },
    prompt: '模拟 HR 说预算有限，我该怎么回应？请给我可直接说出口的话术。',
    icon: MessageOutlined,
  },
  {
    id: 'signing-bonus',
    get group() { return t("谈薪"); },
    get label() { return t("争取更高签字费"); },
    get hint() { return t("生成可直接使用的话术"); },
    prompt: '帮我准备争取更高签字费的话术。',
    icon: SolutionOutlined,
  },
  {
    id: 'compare',
    group: 'Offer',
    get label() { return t("对比手上的 Offer"); },
    get hint() { return t("按总包 / 成长 / 风险综合对比"); },
    prompt: '对比我手上的几个 offer，哪个更值得接受？',
    icon: SwapOutlined,
  },
  {
    id: 'red-line',
    get group() { return t("谈薪"); },
    get label() { return t("谈薪红线自检"); },
    get hint() { return t("哪些话不能说、底线在哪"); },
    prompt: '谈薪时有哪些红线和底线是我必须守住的？帮我做个自检清单。',
    icon: FlagOutlined,
  },
];

export function capabilitiesForMode(isNego: boolean): Capability[] {
  return isNego ? NEGO_CAPABILITIES : GENERAL_CAPABILITIES;
}

export { AudioOutlined };

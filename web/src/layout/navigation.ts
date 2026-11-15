import { t } from '@/i18n';
export type ViewMode =
  | 'dashboard'
  | 'board'
  | 'applications-list'
  | 'calendar'
  | 'reminders'
  | 'interview'
  | 'reviews'
  | 'offers'
  | 'knowledge'
  | 'questions'
  | 'resumes'
  | 'pilot'
  | 'settings';

export type ModuleKey =
  | 'today'
  | 'applications'
  | 'interview'
  | 'offers'
  | 'resources'
  | 'pilot'
  | 'settings';

export type NavigationGroupKey = 'primary' | 'resources' | 'utility';

export interface NavigationGroup {
  key: NavigationGroupKey;
  label: string;
}

export interface ModuleNavItem {
  key: ModuleKey;
  label: string;
  defaultView: ViewMode;
  group: NavigationGroupKey;
}

export interface ModuleTabItem {
  view: ViewMode;
  label: string;
}

export const NAVIGATION_GROUPS: NavigationGroup[] = [
  { key: 'primary', get label() { return t("主要任务"); } },
  { key: 'resources', get label() { return t("常用资料"); } },
  { key: 'utility', get label() { return t("辅助"); } },
];

export const MODULE_NAV: ModuleNavItem[] = [
  { key: 'today', get label() { return t("今日"); }, defaultView: 'dashboard', group: 'primary' },
  { key: 'applications', get label() { return t("投递"); }, defaultView: 'board', group: 'primary' },
  { key: 'interview', get label() { return t("面试"); }, defaultView: 'interview', group: 'primary' },
  { key: 'offers', label: 'Offer', defaultView: 'offers', group: 'primary' },
  { key: 'resources', get label() { return t("素材库"); }, defaultView: 'resumes', group: 'resources' },
  { key: 'settings', get label() { return t("设置"); }, defaultView: 'settings', group: 'utility' },
];

export const MODULE_TABS: Record<ModuleKey, ModuleTabItem[]> = {
  today: [
    { view: 'dashboard', get label() { return t("概览"); } },
    { view: 'reminders', get label() { return t("今日待办"); } },
    { view: 'calendar', get label() { return t("日历"); } },
  ],
  applications: [
    { view: 'board', get label() { return t("看板"); } },
    { view: 'applications-list', get label() { return t("列表"); } },
  ],
  interview: [
    { view: 'interview', get label() { return t("面试"); } },
    { view: 'questions', get label() { return t("刷题"); } },
  ],
  offers: [
    { view: 'offers', label: 'Offer' },
  ],
  resources: [
    { view: 'resumes', get label() { return t("简历"); } },
    { view: 'reviews', get label() { return t("我的经历"); } },
    { view: 'knowledge', get label() { return t("参考知识"); } },
  ],
  pilot: [{ view: 'pilot', get label() { return t("会话中心"); } }],
  settings: [{ view: 'settings', get label() { return t("设置"); } }],
};

const VIEW_TO_MODULE: Partial<Record<ViewMode, ModuleKey>> = {
  dashboard: 'today',
  reminders: 'today',
  resumes: 'resources',
  reviews: 'resources',
  knowledge: 'resources',
  questions: 'interview',
  board: 'applications',
  'applications-list': 'applications',
  calendar: 'today',
  offers: 'offers',
  interview: 'interview',
  pilot: 'pilot',
  settings: 'settings',
};

const DEFAULT_VIEW_BY_MODULE: Record<ModuleKey, ViewMode> = {
  today: 'dashboard',
  applications: 'board',
  interview: 'interview',
  offers: 'offers',
  resources: 'resumes',
  pilot: 'pilot',
  settings: 'settings',
};

export function resolveModuleForView(view: ViewMode): ModuleKey {
  const module = VIEW_TO_MODULE[view];
  if (!module) throw new Error(`View ${view} is not part of v0.1 navigation`);
  return module;
}

export function defaultViewForModule(module: ModuleKey): ViewMode {
  return DEFAULT_VIEW_BY_MODULE[module];
}

export function moduleTabsForView(view: ViewMode): ModuleTabItem[] {
  return MODULE_TABS[resolveModuleForView(view)];
}

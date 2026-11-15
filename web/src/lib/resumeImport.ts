import { t } from '@/i18n';
import type { ResumeImportField } from '@/types/resume';

const LABELS: Record<string, string> = { get contact() { return t("基本信息"); }, get name() { return t("姓名/名称"); }, get email() { return t("邮箱"); }, get phone() { return t("电话"); }, get location() { return t("所在地"); }, get career_intent() { return t("求职意向"); }, get target_roles() { return t("目标岗位"); }, get target_locations() { return t("目标城市"); }, get education() { return t("教育经历"); }, get school() { return t("学校"); }, get degree() { return t("学历"); }, get major() { return t("专业"); }, get start_date() { return t("开始时间", undefined, "resume.startDate"); }, get end_date() { return t("结束时间"); }, get experience() { return t("工作经历"); }, get company() { return t("公司"); }, get title() { return t("职位"); }, get highlights() { return t("工作亮点"); }, get projects() { return t("项目经历"); }, get role() { return t("职责"); }, get skills() { return t("技能"); } };
export function importFieldLabel(path: string): string {
  return path.split('.').reduce<string[]>((parts, part) => {
    if (/^\d+$/.test(part)) parts[parts.length - 1] += ` ${Number(part) + 1}`;
    else parts.push(LABELS[part] ?? t("字段"));
    return parts;
  }, []).join(' · ');
}

export function isImportFieldProtected(content: unknown, path: string): boolean {
  let current: unknown = content;
  for (const segment of path.split('.')) {
    if (Array.isArray(current)) return current.length > 0;
    if (current === undefined || current === null) return false;
    if (typeof current === 'string' && !current.trim()) return /^\d+$/.test(segment);
    if (typeof current !== 'object') return true;
    current = (current as Record<string, unknown>)[segment];
  }
  return current !== undefined && current !== null && !(typeof current === 'string' && !current.trim());
}

// Removing an entire entry or a highlight must not leave sparse server arrays.
export function reindexImportFields(fields: readonly ResumeImportField[]): ResumeImportField[] {
  const indexes = new Map<string, Map<string, number>>();
  return [...fields].sort((a, b) => a.path.localeCompare(b.path, 'en', { numeric: true })).map((field) => {
    const source = field.path.split('.');
    const path = source.map((part, position) => {
      if (!/^\d+$/.test(part)) return part;
      const parent = source.slice(0, position).join('.');
      const siblings = indexes.get(parent) ?? new Map<string, number>();
      if (!siblings.has(part)) siblings.set(part, siblings.size);
      indexes.set(parent, siblings);
      return String(siblings.get(part));
    }).join('.');
    return { ...field, path };
  });
}

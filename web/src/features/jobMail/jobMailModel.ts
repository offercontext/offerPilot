import type { JobMailFields, JobMailSuggestion } from '@/types/jobMail';

export const MAIL_FIELD_LABELS: Record<string, string> = {
  id: '事件 ID', application_id: '投递 ID', event_type: '事件类型', subtype: '子类型',
  scheduled_at: '开始时间（含时区）', duration_minutes: '时长（分钟）', location: '地点／会议链接',
  notes: '备注', round: '轮次', tags: '标签', remind_at: '提醒时间', status: '状态',
  created_at: '创建时间', updated_at: '更新时间', company_name: '公司', position_name: '岗位',
};
export function fieldLabel(field: string) { return MAIL_FIELD_LABELS[field] ?? field; }
export function displayMailValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '未提供';
  if (Array.isArray(value)) return value.map(displayMailValue).join('、') || '无';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
export function mailStatusLabel(status: JobMailSuggestion['status']): string {
  return { pending: '待确认', manual_required: '待补充／人工处理', applied: '已处理', ignored: '已忽略' }[status];
}
export function mailActionLabel(action: JobMailSuggestion['action']): string {
  return { create_event: '新增安排', update_event: '更新已有安排', manual_only: '需要人工处理' }[action];
}
export function validateMailFields(fields: JobMailFields): string | null {
  if (!fields.event_type || !['interview', 'written_test', 'custom'].includes(fields.event_type)) return '首批仅支持完整时间的面试、笔试／测评或自定义事件。';
  if (!fields.scheduled_at || !/T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(fields.scheduled_at) || !Number.isFinite(Date.parse(fields.scheduled_at))) {
    return '请填写明确年份、日期、时间及时区，例如 2026-10-15T15:00:00+08:00。';
  }
  if (!Number.isInteger(fields.duration_minutes) || (fields.duration_minutes ?? 0) <= 0) return '请核实并填写正整数时长；不会默认补成 60 分钟。';
  return null;
}

const RECOVERY_KEY = 'offerpilot:job-mail:pending-operations';
export function readMailRecovery(): Record<string, string> {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(RECOVERY_KEY) ?? '{}');
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([id, operation]) => /^[a-f\d-]{36}$/i.test(id) && typeof operation === 'string' && /^[a-f\d-]{36}$/i.test(operation)));
  } catch { return {}; }
}
export function saveMailRecovery(suggestionId: string, operationId: string | null): boolean {
  try {
    const next = readMailRecovery();
    if (operationId === null) delete next[String(suggestionId)];
    else next[String(suggestionId)] = operationId;
    sessionStorage.setItem(RECOVERY_KEY, JSON.stringify(next));
    return true;
  } catch { return false; }
}
export function isDefiniteMailRejection(error: unknown): boolean {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return [400, 401, 403, 404, 409, 422].includes(status ?? 0);
}
export function mailErrorText(error: unknown, fallback: string): string {
  const response = (error as { response?: { status?: number; data?: { error_code?: string } } })?.response;
  const code = response?.data?.error_code;
  const status = response?.status;
  if (code === 'mail_scope_changed') return '邮箱已断开或读取范围已改变，旧建议不能确认。请重新导入或同步，再核对新的建议。';
  if (code === 'mail_evidence_unavailable') return '原文已清理，不能使用旧建议确认。请重新提供邮件原文后核对。';
  if (status === 409) return '建议、投递、目标记录或邮箱范围已变化。本次未应用，请关闭后重新审阅最新内容。';
  if (status === 422 || status === 400) return '输入或预览已失效，请核对完整时间、时长与目标后重新预览。';
  if (status === 401 || status === 403) return '没有权限执行此操作，请检查当前工作区登录状态。';
  return fallback;
}

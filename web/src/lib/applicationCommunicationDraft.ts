import { t } from '@/i18n';
import type { Application } from '@/types/application';
import type { ApplicationOutcome, ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import type { ScheduleEvent } from '@/types/event';

export type ApplicationCommunicationDraftKind = 'follow_up' | 'thank_you';

export interface ApplicationCommunicationDraftInput {
  kind: ApplicationCommunicationDraftKind;
  application: Application;
  snapshot: ApplicationSubmissionSnapshot;
  outcome?: ApplicationOutcome | null;
  event?: ScheduleEvent | null;
  recipientName?: string;
  highlight?: string;
  timeZone: string;
}

export interface ApplicationCommunicationDraft {
  subject: string;
  body: string;
  evidenceLabels: string[];
}

const STAGE_LABELS: Record<ApplicationOutcome['stage'], string> = {
  get applied() { return t("已投递"); },
  get screening() { return t("筛选沟通"); },
  get written_test() { return t("笔试"); },
  get interview() { return t("面试"); },
  offer: 'Offer',
  get closed() { return t("流程结束"); },
};

const RESULT_LABELS: Record<ApplicationOutcome['result'], string> = {
  get advanced() { return t("进入下一阶段"); },
  get rejected() { return t("未通过"); },
  get withdrawn() { return t("主动退出"); },
  get no_response() { return t("暂无回复"); },
  get offer_received() { return t("收到 Offer"); },
  get other() { return t("其他"); },
};

export type CommunicationDateSource = 'archive_utc' | 'event_rfc3339';

const TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:\d{2})?$/;

export function applicationCommunicationCalendarDate(
  value: string,
  timeZone: string,
  source: CommunicationDateSource,
): string | null {
  const match = typeof value === 'string' ? TIMESTAMP.exec(value) : null;
  if (!match || match[0] !== value) return null;
  const [, y, m, d, h, min, sec, offset] = match;
  // Only submission/outcome archive timestamps are confirmed legacy bare UTC.
  // A bare scheduled event is missing its wire time zone and must stay unknown.
  if (!offset && source !== 'archive_utc') return null;
  const year = Number(y);
  const month = Number(m);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const monthDays = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (year < 1 || month < 1 || month > 12 || Number(d) < 1 || Number(d) > monthDays[month - 1]
    || Number(h) > 23 || Number(min) > 59 || Number(sec) > 59
    || (offset && offset !== 'Z' && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(4)) > 59))) return null;
  // Date.parse is safe after strict shape and calendar validation; Intl uses the
  // offset at this instant, not today's offset (DST or historical transitions).
  const instant = Date.parse(offset ? value : value + 'Z');
  if (!Number.isFinite(instant)) return null;
  try {
    const parts = new Intl.DateTimeFormat('en', {
      timeZone, calendar: 'gregory', numberingSystem: 'latn',
      year: 'numeric', month: '2-digit', day: '2-digit', era: 'short',
    }).formatToParts(instant);
    const read = (type: Intl.DateTimeFormatPartTypes) => parts.find(part => part.type === type)?.value;
    const localYear = read('year');
    const localMonth = read('month');
    const localDay = read('day');
    return read('era') === 'AD' && localYear && /^[0-9]{1,4}$/.test(localYear) && localMonth && localDay
      ? `${localYear.padStart(4, '0')}-${localMonth}-${localDay}` : null;
  } catch {
    return null;
  }
}

function localizedDraftDate(date: string): string {
  const [year, month, day] = date.split('-').map(Number);
  return t("{value0}年{value1}月{value2}日", { value0: year, value1: month, value2: day });
}

function greeting(recipientName?: string): string {
  const recipient = recipientName?.trim();
  return recipient ? t("{value0}，您好：", { value0: recipient }) : t("您好：");
}

function normalizedHighlight(value?: string): string {
  const highlight = value?.trim() ?? '';
  if (Array.from(highlight).length > 240) throw new Error(t("交流亮点不能超过 240 个字符"));
  return highlight;
}

export function isThankYouDraftAvailable(
  outcomes: ApplicationOutcome[],
): boolean {
  return outcomes.some((outcome) => outcome.stage === 'interview');
}

// 业务地图：docs/architecture/code-map/applications.md。这里只从选定事实拼出可编辑文案；
// 不调用模型、不发送邮件；感谢信仍要核对投递档案、面试结果与可选日程的归属。
export function buildApplicationCommunicationDraft(
  input: ApplicationCommunicationDraftInput,
): ApplicationCommunicationDraft {
  const { application, snapshot, outcome, event } = input;
  if (snapshot.application_id !== application.id) throw new Error(t("投递档案与当前投递不匹配"));
  if (input.kind === 'thank_you') {
    if (!outcome || outcome.stage !== 'interview') throw new Error(t("请选择一条已记录的面试结果"));
    if (outcome.application_id !== application.id || outcome.submission_snapshot_id !== snapshot.id) {
      throw new Error(t("面试结果不属于所选投递档案"));
    }
    if (event && (event.application_id !== application.id || outcome.application_event_id !== event.id)) {
      throw new Error(t("面试日程与结果记录不匹配"));
    }
    if (event && (event.event_type !== 'interview' || ['cancelled', 'deleted', 'soft_deleted'].includes(event.status))) {
      throw new Error(t("面试日程不可用于感谢信"));
    }
  }
  const highlight = normalizedHighlight(input.highlight);
  const submittedDate = applicationCommunicationCalendarDate(snapshot.submitted_at, input.timeZone, 'archive_utc');
  const position = application.position_name.trim() || t("应聘岗位");
  const evidenceLabels = [
    t("投递档案 #{value0} · {value1} · JD v{value2}", { value0: snapshot.id, value1: snapshot.resume_title, value2: snapshot.jd_version_number }),
  ];
  if (submittedDate) evidenceLabels.push(t("投递时间 · {value0}", { value0: submittedDate }));
  if (outcome) evidenceLabels.push(t("结果 #{value0} · {value1} · {value2}", { value0: outcome.id, value1: STAGE_LABELS[outcome.stage], value2: RESULT_LABELS[outcome.result] }));
  if (event) {
    const eventDate = applicationCommunicationCalendarDate(event.scheduled_at, input.timeZone, 'event_rfc3339');
    const round = Number.isInteger(event.round) && event.round > 0 ? t("第 {value0} 轮", { value0: event.round }) : '';
    const eventName = event.event_type === 'interview' ? t("{value0}面试", { value0: round }) : t("关联日程");
    evidenceLabels.push(t("日程 #{value0} · {value1}{value2}", { value0: event.id, value1: eventName, value2: eventDate ? ` · ${eventDate}` : '' }));
  }
  if (highlight) evidenceLabels.push(t("用户确认亮点"));
  evidenceLabels.push(t("当前投递信息 · {value0} · {value1}", { value0: application.company_name.trim() || t('公司未填写'), value1: position }));

  if (input.kind === 'thank_you') {
    const highlightParagraph = highlight
      ? t("本次交流中，关于「{value0}」的讨论让我对这一岗位有了更具体的了解。", { value0: highlight })
      : t("本次交流让我进一步了解了这一岗位。");
    return {
      subject: t("感谢「{value0}」面试交流", { value0: position }),
      body: [
        greeting(input.recipientName),
        '',
        t("感谢您安排并参与贵公司「{value0}」职位的面试交流。", { value0: position }),
        highlightParagraph,
        t("我依然对这个机会很感兴趣。如需补充任何材料，我会及时提供。"),
        '',
        t("再次感谢您的时间。"),
      ].join('\n'),
      evidenceLabels,
    };
  }

  const submittedParagraph = submittedDate
    ? t("我于 {value0}提交了贵公司「{value1}」职位的申请，想礼貌了解目前的招聘进展。", { value0: localizedDraftDate(submittedDate), value1: position })
    : t("我此前提交了贵公司「{value0}」职位的申请，想礼貌了解目前的招聘进展。", { value0: position });
  return {
    subject: t("关于「{value0}」申请进展的跟进", { value0: position }),
    body: [
      greeting(input.recipientName),
      '',
      submittedParagraph,
      t("我仍然对这个机会很感兴趣。如需补充任何材料，我会及时提供。"),
      '',
      t("感谢您的时间，期待您的回复。"),
    ].join('\n'),
    evidenceLabels,
  };
}

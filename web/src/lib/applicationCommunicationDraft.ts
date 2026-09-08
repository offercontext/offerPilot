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
  timezoneOffsetMinutes: number;
}

export interface ApplicationCommunicationDraft {
  subject: string;
  body: string;
  evidenceLabels: string[];
}

const STAGE_LABELS: Record<ApplicationOutcome['stage'], string> = {
  applied: '已投递',
  screening: '筛选沟通',
  written_test: '笔试',
  interview: '面试',
  offer: 'Offer',
  closed: '流程结束',
};

const RESULT_LABELS: Record<ApplicationOutcome['result'], string> = {
  advanced: '进入下一阶段',
  rejected: '未通过',
  withdrawn: '主动退出',
  no_response: '暂无回复',
  offer_received: '收到 Offer',
  other: '其他',
};

function safeCalendarDate(value: string, timezoneOffsetMinutes: number): string | null {
  if (!Number.isFinite(timezoneOffsetMinutes)) return null;
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return null;
  const local = new Date(instant - timezoneOffsetMinutes * 60_000);
  const year = local.getUTCFullYear();
  const month = String(local.getUTCMonth() + 1).padStart(2, '0');
  const day = String(local.getUTCDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function chineseDate(value: string, timezoneOffsetMinutes: number): string | null {
  const date = safeCalendarDate(value, timezoneOffsetMinutes);
  if (!date) return null;
  const [year, month, day] = date.split('-').map(Number);
  return `${year}年${month}月${day}日`;
}

function greeting(recipientName?: string): string {
  const recipient = recipientName?.trim();
  return recipient ? `${recipient}，您好：` : '您好：';
}

function normalizedHighlight(value?: string): string {
  const highlight = value?.trim() ?? '';
  if (Array.from(highlight).length > 240) throw new Error('交流亮点不能超过 240 个字符');
  return highlight;
}

export function isThankYouDraftAvailable(
  outcomes: ApplicationOutcome[],
): boolean {
  return outcomes.some((outcome) => outcome.stage === 'interview');
}

export function buildApplicationCommunicationDraft(
  input: ApplicationCommunicationDraftInput,
): ApplicationCommunicationDraft {
  const { application, snapshot, outcome, event } = input;
  if (snapshot.application_id !== application.id) throw new Error('投递档案与当前投递不匹配');
  if (input.kind === 'thank_you') {
    if (!outcome || outcome.stage !== 'interview') throw new Error('请选择一条已记录的面试结果');
    if (outcome.application_id !== application.id || outcome.submission_snapshot_id !== snapshot.id) {
      throw new Error('面试结果不属于所选投递档案');
    }
    if (event && (event.application_id !== application.id || outcome.application_event_id !== event.id)) {
      throw new Error('面试日程与结果记录不匹配');
    }
    if (event && (event.event_type !== 'interview' || ['cancelled', 'deleted', 'soft_deleted'].includes(event.status))) {
      throw new Error('面试日程不可用于感谢信');
    }
  }
  const highlight = normalizedHighlight(input.highlight);
  const submittedDate = safeCalendarDate(snapshot.submitted_at, input.timezoneOffsetMinutes);
  const submittedDateChinese = chineseDate(snapshot.submitted_at, input.timezoneOffsetMinutes);
  const position = application.position_name.trim() || '应聘岗位';
  const evidenceLabels = [
    `投递档案 #${snapshot.id} · ${snapshot.resume_title} · JD v${snapshot.jd_version_number}`,
  ];
  if (submittedDate) evidenceLabels.push(`投递时间 · ${submittedDate}`);
  if (outcome) evidenceLabels.push(`结果 #${outcome.id} · ${STAGE_LABELS[outcome.stage]} · ${RESULT_LABELS[outcome.result]}`);
  if (event) {
    const eventDate = safeCalendarDate(event.scheduled_at, input.timezoneOffsetMinutes);
    const round = Number.isInteger(event.round) && event.round > 0 ? `第 ${event.round} 轮` : '';
    const eventName = event.event_type === 'interview' ? `${round}面试` : '关联日程';
    evidenceLabels.push(`日程 #${event.id} · ${eventName}${eventDate ? ` · ${eventDate}` : ''}`);
  }
  if (highlight) evidenceLabels.push('用户确认亮点');
  evidenceLabels.push(`当前投递信息 · ${application.company_name.trim() || '公司未填写'} · ${position}`);

  if (input.kind === 'thank_you') {
    const highlightParagraph = highlight
      ? `本次交流中，关于「${highlight}」的讨论让我对这一岗位有了更具体的了解。`
      : '本次交流让我进一步了解了这一岗位。';
    return {
      subject: `感谢「${position}」面试交流`,
      body: [
        greeting(input.recipientName),
        '',
        `感谢您安排并参与贵公司「${position}」职位的面试交流。`,
        highlightParagraph,
        '我依然对这个机会很感兴趣。如需补充任何材料，我会及时提供。',
        '',
        '再次感谢您的时间。',
      ].join('\n'),
      evidenceLabels,
    };
  }

  const submittedParagraph = submittedDateChinese
    ? `我于 ${submittedDateChinese}提交了贵公司「${position}」职位的申请，想礼貌了解目前的招聘进展。`
    : `我此前提交了贵公司「${position}」职位的申请，想礼貌了解目前的招聘进展。`;
  return {
    subject: `关于「${position}」申请进展的跟进`,
    body: [
      greeting(input.recipientName),
      '',
      submittedParagraph,
      '我仍然对这个机会很感兴趣。如需补充任何材料，我会及时提供。',
      '',
      '感谢您的时间，期待您的回复。',
    ].join('\n'),
    evidenceLabels,
  };
}

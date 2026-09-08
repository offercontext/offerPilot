import { useEffect, useMemo, useState } from 'react';
import { App, Button, Input, Select, Tag } from 'antd';
import {
  CheckCircleFilled,
  CopyOutlined,
  FileTextOutlined,
  MailOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import type { Application } from '@/types/application';
import type { ApplicationOutcome, ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import type { ScheduleEvent } from '@/types/event';
import {
  buildApplicationCommunicationDraft,
  isThankYouDraftAvailable,
  type ApplicationCommunicationDraft,
  type ApplicationCommunicationDraftKind,
} from '@/lib/applicationCommunicationDraft';
import styles from './ApplicationCommunicationDraftPanel.module.css';

interface Props {
  application: Application;
  snapshots: ApplicationSubmissionSnapshot[];
  outcomes: ApplicationOutcome[];
  events: ScheduleEvent[];
}

const RESULT_LABELS: Record<ApplicationOutcome['result'], string> = {
  advanced: '进入下一阶段',
  rejected: '未通过',
  withdrawn: '主动退出',
  no_response: '暂无回复',
  offer_received: '收到 Offer',
  other: '其他',
};

function dateLabel(value: string, timezoneOffsetMinutes: number): string {
  const instant = Date.parse(value);
  if (!Number.isFinite(instant)) return '日期未知';
  const local = new Date(instant - timezoneOffsetMinutes * 60_000);
  const year = local.getUTCFullYear();
  const month = String(local.getUTCMonth() + 1).padStart(2, '0');
  const day = String(local.getUTCDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export default function ApplicationCommunicationDraftPanel({ application, snapshots, outcomes, events }: Props) {
  const { message } = App.useApp();
  const [kind, setKind] = useState<ApplicationCommunicationDraftKind>('follow_up');
  const [snapshotId, setSnapshotId] = useState<number | undefined>(snapshots[0]?.id);
  const [outcomeId, setOutcomeId] = useState<number | undefined>();
  const [eventId, setEventId] = useState<number | undefined>();
  const [recipientName, setRecipientName] = useState('');
  const [highlight, setHighlight] = useState('');
  const [draft, setDraft] = useState<ApplicationCommunicationDraft | null>(null);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [validationError, setValidationError] = useState('');
  const [copyStatus, setCopyStatus] = useState('');
  const timezoneOffsetMinutes = new Date().getTimezoneOffset();

  const selectedSnapshot = snapshots.find((snapshot) => snapshot.id === snapshotId) ?? null;
  const interviewOutcomes = useMemo(
    () => outcomes.filter((outcome) => outcome.stage === 'interview' && outcome.submission_snapshot_id === snapshotId),
    [outcomes, snapshotId],
  );
  const thankYouAvailable = isThankYouDraftAvailable(interviewOutcomes);
  const selectedOutcome = outcomes.find((outcome) => outcome.id === outcomeId) ?? null;
  const selectedEvent = events.find((event) => event.id === eventId) ?? null;

  useEffect(() => {
    if (snapshotId && snapshots.some((snapshot) => snapshot.id === snapshotId)) return;
    setSnapshotId(snapshots[0]?.id);
    setDraft(null);
  }, [snapshotId, snapshots]);
  useEffect(() => {
    if (!outcomeId || interviewOutcomes.some((outcome) => outcome.id === outcomeId)) return;
    setOutcomeId(undefined);
    setEventId(undefined);
    setDraft(null);
  }, [interviewOutcomes, outcomeId]);
  useEffect(() => {
    if (!eventId) return;
    const eventStillValid = events.some((event) => event.id === eventId
      && event.application_id === application.id
      && event.event_type === 'interview'
      && selectedOutcome?.application_event_id === event.id
      && !['cancelled', 'deleted', 'soft_deleted'].includes(event.status));
    if (eventStillValid) return;
    setEventId(undefined);
    setDraft(null);
    setSubject('');
    setBody('');
    setValidationError('');
    setCopyStatus('');
  }, [application.id, eventId, events, selectedOutcome?.application_event_id]);

  const invalidateDraft = () => {
    setDraft(null);
    setSubject('');
    setBody('');
    setValidationError('');
  };

  const selectKind = (nextKind: ApplicationCommunicationDraftKind) => {
    if (nextKind === 'thank_you' && !thankYouAvailable) return;
    setKind(nextKind);
    if (nextKind === 'follow_up') {
      setOutcomeId(undefined);
      setEventId(undefined);
      setHighlight('');
    }
    invalidateDraft();
  };

  const generate = () => {
    if (!selectedSnapshot) return;
    try {
      const next = buildApplicationCommunicationDraft({
        kind,
        application,
        snapshot: selectedSnapshot,
        outcome: selectedOutcome,
        event: selectedEvent,
        recipientName,
        highlight,
        timezoneOffsetMinutes,
      });
      setDraft(next);
      setSubject(next.subject);
      setBody(next.body);
      setValidationError('');
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : '无法生成草稿，请检查输入');
    }
  };

  const restore = () => {
    if (!draft) return;
    setSubject(draft.subject);
    setBody(draft.body);
  };

  const copy = async () => {
    setCopyStatus('');
    try {
      await navigator.clipboard.writeText(`${subject}\n\n${body}`);
      setCopyStatus('完整草稿已复制');
      message.success('完整草稿已复制');
    } catch {
      setCopyStatus('复制失败，请手动选择文字');
      message.error('复制失败，请手动选择文字');
    }
  };

  return (
    <section className={styles.panel} aria-labelledby="communication-draft-title">
      <div className={styles.header}>
        <div>
          <p className={styles.eyebrow}>COMMUNICATION DRAFT</p>
          <h3 id="communication-draft-title"><MailOutlined /> 跟进与感谢信</h3>
          <p>使用已冻结的投递档案、当前投递名称和你明确填写的内容；生成后可编辑、复制，不会自动发送。</p>
        </div>
        <Tag className={styles.localTag} icon={<SafetyCertificateOutlined />}>本地确定性草稿</Tag>
      </div>

      <div className={styles.kindSwitch} role="group" aria-label="草稿类型">
        <button type="button" aria-pressed={kind === 'follow_up'} className={kind === 'follow_up' ? styles.kindActive : ''} onClick={() => selectKind('follow_up')}>
          <span className={styles.kindIcon}><MailOutlined /></span>
          <span><strong>投递跟进信</strong><small>礼貌询问当前进展</small></span>
        </button>
        <button type="button" aria-pressed={kind === 'thank_you'} aria-disabled={!thankYouAvailable} aria-describedby={!thankYouAvailable ? 'thank-you-unavailable' : undefined} className={kind === 'thank_you' ? styles.kindActive : ''} onClick={() => selectKind('thank_you')}>
          <span className={styles.kindIcon}><CheckCircleFilled /></span>
          <span><strong>面试感谢信</strong><small id="thank-you-unavailable">{thankYouAvailable ? '基于已记录的面试结果' : '记录面试结果后可使用'}</small></span>
        </button>
      </div>

      <div className={styles.workspace}>
        <div className={styles.sourceColumn}>
          <div className={styles.columnTitle}><span>01</span><div><strong>确认来源</strong><small>选择本次草稿所依据的事实</small></div></div>
          <label className={styles.fieldLabel} htmlFor="communication-snapshot">投递档案</label>
          <Select
            id="communication-snapshot"
            value={snapshotId}
            className={styles.fullWidth}
            onChange={(value) => { setSnapshotId(value); invalidateDraft(); }}
            options={snapshots.map((snapshot) => ({
              value: snapshot.id,
              label: `${dateLabel(snapshot.submitted_at, timezoneOffsetMinutes)} · ${snapshot.resume_title} · JD v${snapshot.jd_version_number}`,
            }))}
          />
          {selectedSnapshot ? (
            <div className={styles.sourceCard}>
              <div className={styles.sourceCardTop}><FileTextOutlined /><strong>档案 #{selectedSnapshot.id}</strong><Tag>{selectedSnapshot.source_kind === 'pilot' ? 'Pilot 确认' : 'UI 保存'}</Tag></div>
              <dl>
                <div><dt>简历</dt><dd>{selectedSnapshot.resume_title}</dd></div>
                <div><dt>岗位资料</dt><dd>JD v{selectedSnapshot.jd_version_number}</dd></div>
                <div><dt>投递时间</dt><dd>{dateLabel(selectedSnapshot.submitted_at, timezoneOffsetMinutes)}</dd></div>
              </dl>
            </div>
          ) : null}

          {kind === 'thank_you' ? (
            <div className={styles.optionalSources}>
              <label className={styles.fieldLabel} htmlFor="communication-outcome">面试结果（必选）</label>
              <Select
                id="communication-outcome"
                aria-label="面试结果"
                value={outcomeId}
                className={styles.fullWidth}
                placeholder="请选择一条已记录的面试结果"
                onChange={(value) => {
                  const nextOutcome = interviewOutcomes.find((outcome) => outcome.id === value);
                  const linkedEvent = events.find((event) => event.id === nextOutcome?.application_event_id
                    && event.event_type === 'interview'
                    && !['cancelled', 'deleted', 'soft_deleted'].includes(event.status));
                  setOutcomeId(value);
                  setEventId(linkedEvent?.id);
                  invalidateDraft();
                }}
                options={interviewOutcomes.map((outcome) => ({ value: outcome.id, label: `${dateLabel(outcome.occurred_at, timezoneOffsetMinutes)} · ${RESULT_LABELS[outcome.result]}` }))}
              />
              {selectedEvent ? <div className={styles.linkedEvent}><span>关联日程</span><strong>第 {selectedEvent.round} 轮面试 · {dateLabel(selectedEvent.scheduled_at, timezoneOffsetMinutes)}</strong></div> : null}
            </div>
          ) : null}

          <label className={styles.fieldLabel} htmlFor="communication-recipient">收件人称呼（可选）</label>
          <Input
            id="communication-recipient"
            aria-label="收件人称呼"
            value={recipientName}
            maxLength={80}
            placeholder="例如：林女士；留空则使用“您好”"
            onChange={(event) => { setRecipientName(event.target.value); invalidateDraft(); }}
          />
          {kind === 'thank_you' ? (
            <>
              <label className={styles.fieldLabel} htmlFor="communication-highlight">本次交流亮点（可选）</label>
              <Input.TextArea
                id="communication-highlight"
                aria-label="交流亮点"
                value={highlight}
                rows={3}
                showCount
                maxLength={240}
                placeholder="只填写你确认过的具体讨论，例如：服务稳定性与可观测性"
                onChange={(event) => { setHighlight(event.target.value); invalidateDraft(); }}
              />
            </>
          ) : null}
          {validationError ? <p className={styles.error} role="alert">{validationError}</p> : null}
          <Button className={styles.generateButton} type="primary" icon={<FileTextOutlined />} block disabled={!selectedSnapshot || (kind === 'thank_you' && !selectedOutcome)} onClick={generate}>生成草稿</Button>
        </div>

        <div className={styles.editorColumn}>
          <div className={styles.columnTitle}><span>02</span><div><strong>编辑与复制</strong><small>内容只保留在当前页面</small></div></div>
          {!draft ? (
            <div className={styles.blankLetter}>
              <div className={styles.blankIcon}><MailOutlined /></div>
              <strong>草稿尚未生成</strong>
              <p>确认左侧来源后生成；不会调用 AI，也不会创建业务记录。</p>
            </div>
          ) : (
            <div className={styles.letter}>
              <div className={styles.evidenceHeader}>
                <span><SafetyCertificateOutlined /> 本次草稿使用的来源</span>
                <div>{draft.evidenceLabels.map((label) => <Tag key={label}>{label}</Tag>)}</div>
              </div>
              <label className={styles.fieldLabel} htmlFor="communication-subject">主题</label>
              <Input id="communication-subject" aria-label="邮件主题" value={subject} maxLength={160} onChange={(event) => { setSubject(event.target.value); setCopyStatus(''); }} />
              <label className={styles.fieldLabel} htmlFor="communication-body">正文</label>
              <Input.TextArea id="communication-body" aria-label="邮件正文" value={body} rows={11} onChange={(event) => { setBody(event.target.value); setCopyStatus(''); }} />
              <div className={styles.editorActions}>
                <Button icon={<ReloadOutlined />} onClick={restore}>恢复系统草稿</Button>
                <Button type="primary" icon={<CopyOutlined />} disabled={!subject.trim() || !body.trim()} onClick={() => void copy()}>复制完整内容</Button>
              </div>
            </div>
          )}
        </div>
      </div>
      <p className={styles.srOnly} aria-live="polite">{copyStatus}</p>
    </section>
  );
}

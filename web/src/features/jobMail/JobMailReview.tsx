import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal, Skeleton } from 'antd';
import { listApplications } from '@/services/applications';
import { listEvents } from '@/services/events';
import { confirmJobMail, getJobMailReceipt, getJobMailSuggestion, ignoreJobMail, JOB_MAIL_QUERY_KEY, previewJobMail } from '@/services/jobMail';
import type { JobMailFields, JobMailPreview, JobMailPreviewInput, JobMailReceipt, JobMailSuggestion } from '@/types/jobMail';
import type { ViewMode } from '@/layout/navigation';
import { displayMailField, displayMailTime, fieldLabel, isDefiniteMailRejection, mailActionLabel, mailErrorText, mailFieldErrors, mergeMailFields, readMailRecovery, saveMailRecovery, validateMailFields } from './jobMailModel';
import styles from './jobMail.module.css';

interface Props {
  suggestionId: string;
  onClose: () => void;
  onOpenRecord: (applicationId: number) => void;
  onNavigate: (view: ViewMode) => void;
}
export function MailRecord({ value, kind = 'event' }: { value: Record<string, unknown> | null; kind?: 'event' | 'application' }) {
  if (!value || Object.keys(value).length === 0) return <p className={styles.muted}>无现有记录（将新增）</p>;
  return <dl className={styles.details}>{Object.entries(value).map(([key, item]) => <div key={key} style={{ display: 'contents' }}><dt>{key === 'id' && kind === 'application' ? '投递 ID' : fieldLabel(key)}</dt><dd>{displayMailField(key, item)}</dd></div>)}</dl>;
}
function FinalFields({ preview }: { preview: JobMailPreview }) {
  const changedKeys = preview.changes.filter((item) => preview.target_event_id || (item.after !== null && item.after !== undefined && item.after !== '' && item.after !== 0 && (!Array.isArray(item.after) || item.after.length > 0))).map((item) => item.field);
  const keys = [...new Set(['event_type', ...(preview.after.subtype ? ['subtype'] : []), 'scheduled_at', 'duration_minutes', 'location', 'remind_at', ...changedKeys])];
  return <dl className={styles.finalFields}>{keys.map((key) => {
    const change = preview.changes.find((item) => item.field === key);
    const empty = preview.after[key] === null || preview.after[key] === undefined || preview.after[key] === '' || (Array.isArray(preview.after[key]) && preview.after[key].length === 0);
    const removed = !!preview.target_event_id && !!change && empty;
    return <div key={key} className={styles.summaryField}>
      <dt>{fieldLabel(key)}</dt>
      <dd><span className={removed ? styles.removedValue : undefined}>{removed ? (key === 'remind_at' ? '移除提醒' : '清空') : key === 'remind_at' && empty ? '未设置' : displayMailField(key, preview.after[key])}</span>
        {preview.target_event_id && change && <small className={styles.previousValue}>原为：{displayMailField(key, change.before)}</small>}
      </dd>
    </div>;
  })}</dl>;
}
export default function JobMailReview(props: Props) {
  const [processing, setProcessing] = useState(false);
  const query = useQuery({ queryKey: [...JOB_MAIL_QUERY_KEY, 'suggestion', props.suggestionId], queryFn: () => getJobMailSuggestion(props.suggestionId), retry: false });
  return <Modal open title="核对邮件建议" onCancel={props.onClose} footer={null} width={880} maskClosable={false} closable={!processing} keyboard={!processing}>
    {query.isPending ? <Skeleton active /> : query.isError || !query.data ? <Alert type="error" message="建议读取失败" description="未执行任何写入。请重试读取。" action={<Button onClick={() => void query.refetch()}>重试</Button>} /> : <ReviewContent key={`${query.data.id}:${query.data.version}`} suggestion={query.data} onProcessing={setProcessing} {...props} />}
  </Modal>;
}

function ReviewContent({ suggestion, onClose, onOpenRecord, onNavigate, onProcessing }: Props & { suggestion: JobMailSuggestion; onProcessing: (processing: boolean) => void }) {
  const queryClient = useQueryClient();
  const appsQuery = useQuery({ queryKey: ['applications'], queryFn: () => listApplications(), retry: false });
  const [applicationId, setApplicationId] = useState<number | undefined>(() => suggestion.application_candidates.length === 1 ? suggestion.application_candidates[0].id : undefined);
  const [targetEventId, setTargetEventId] = useState<number | undefined>(suggestion.target_event_id ?? undefined);
  const eventsQuery = useQuery({ queryKey: ['events', 'job-mail', applicationId], queryFn: () => listEvents({ application_id: applicationId }), enabled: suggestion.action === 'update_event' && !!applicationId, retry: false });
  const [fields, setFields] = useState<JobMailFields>({});
  const [preview, setPreview] = useState<JobMailPreview | null>(null);
  const [previewInput, setPreviewInput] = useState<JobMailPreviewInput | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [receipt, setReceipt] = useState<JobMailReceipt | null>(suggestion.receipt);
  const [uncertainOperation, setUncertainOperation] = useState(() => readMailRecovery()[String(suggestion.id)] ?? '');
  const [ignoreOpen, setIgnoreOpen] = useState(false);
  useEffect(() => { onProcessing(!!busy); return () => onProcessing(false); }, [busy, onProcessing]);
  const lock = useRef(false);
  const live = useRef(true);
  const generation = useRef(0);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { live.current = true; return () => { live.current = false; generation.current += 1; }; }, []);
  useEffect(() => { if (preview) previewHeading.current?.focus(); }, [preview]);

  const applications = (appsQuery.data ?? []).filter((app) => !app.deleted_at);
  const selectedApp = applications.find((app) => app.id === applicationId);
  const targetEvent = (eventsQuery.data ?? []).find((item) => item.id === targetEventId && item.application_id === applicationId);
  const effectiveFields = mergeMailFields(suggestion.proposed_fields, fields, suggestion.action === 'update_event' ? targetEvent : undefined);
  const fieldErrors = mailFieldErrors(effectiveFields);
  const manualOnly = suggestion.action === 'manual_only' || suggestion.status === 'manual_required';
  const readOnly = !!receipt || suggestion.status === 'ignored' || suggestion.status === 'applied';
  const sourceAvailable = !!suggestion.evidence && !suggestion.evidence.cleared_at && suggestion.evidence?.snippet !== null;
  const canReview = !readOnly && !manualOnly && sourceAvailable;
  const blocked = !!busy || !!uncertainOperation;

  function invalidatePreview() {
    generation.current += 1;
    setPreview(null); setPreviewInput(null); setError('');
  }
  function change<K extends keyof JobMailFields>(key: K, value: JobMailFields[K]) {
    invalidatePreview(); setFields((old) => ({ ...old, [key]: value }));
  }
  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ['events'] }),
      queryClient.invalidateQueries({ queryKey: ['calendar'] }),
      queryClient.invalidateQueries({ queryKey: ['applications'] }),
    ]);
  }
  async function preparePreview() {
    if (lock.current || uncertainOperation || !canReview) return;
    if (!selectedApp) { setError('请从当前投递中明确选择一个目标。没有投递时，请先在投递页手动创建。'); return; }
    if (suggestion.action === 'update_event' && !targetEvent) { setError('请从该投递选择要更新的现有事件。'); return; }
    const validation = validateMailFields(effectiveFields);
    if (validation) { setError(validation); return; }
    invalidatePreview(); const requestGeneration = generation.current;
    const input: JobMailPreviewInput = {
      operation_id: crypto.randomUUID(), suggestion_version: suggestion.version,
      application_id: selectedApp.id, edited_fields: { ...fields },
      ...(suggestion.action === 'update_event' ? { target_event_id: targetEventId } : {}),
    };
    lock.current = true; setBusy('preview');
    try {
      const result = await previewJobMail(suggestion.id, input);
      if (live.current && requestGeneration === generation.current) {
        if (!result.application_snapshot || result.application_snapshot.id !== input.application_id || result.operation_id !== input.operation_id || result.suggestion_id !== suggestion.id || !result.preview_token) {
          setError('预览缺少完整目标快照或不匹配当前请求，不能确认。请刷新后重新审阅。');
          return;
        }
        setPreview(result); setPreviewInput(input);
      }
    } catch (err) {
      if (live.current && requestGeneration === generation.current) setError(mailErrorText(err, '预览失败，未执行写入。请重新核对后预览。'));
    } finally { lock.current = false; if (live.current) setBusy(''); }
  }
  async function apply() {
    if (lock.current || uncertainOperation || !preview || !previewInput) return;
    if (!Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= Date.now()) { invalidatePreview(); setError('预览已过期，请重新生成预览并核对。'); return; }
    if (!saveMailRecovery(suggestion.id, preview.operation_id)) { setError('浏览器无法保存操作恢复标识，暂不提交。请允许会话存储后再试。'); return; }
    lock.current = true; setBusy('confirm'); setError('');
    const operationId = preview.operation_id;
    try {
      const result = await confirmJobMail(suggestion.id, { ...previewInput, preview_token: preview.preview_token, explicit_confirmation: true });
      saveMailRecovery(suggestion.id, null);
      if (live.current) { setReceipt(result); setPreview(null); setPreviewInput(null); }
      await refreshAll();
    } catch (err) {
      if (isDefiniteMailRejection(err)) {
        saveMailRecovery(suggestion.id, null);
        if (live.current) { invalidatePreview(); setError(mailErrorText(err, '提交被拒绝，未应用。请关闭后重新审阅。')); }
      } else if (live.current) {
        setUncertainOperation(operationId);
        setError('提交结果未知。请先核对回执，不要重复确认或重新新增。');
      }
    } finally { lock.current = false; if (live.current) setBusy(''); }
  }
  async function recover() {
    if (lock.current || !uncertainOperation) return;
    lock.current = true; setBusy('receipt'); setError('');
    try {
      const result = await getJobMailReceipt(uncertainOperation);
      saveMailRecovery(suggestion.id, null);
      if (live.current) { setReceipt(result); setUncertainOperation(''); setPreview(null); }
      await refreshAll();
    } catch {
      if (live.current) setError('暂未取得回执，不能据此判定未写入。请稍后继续核对；当前不会重放提交。');
    } finally { lock.current = false; if (live.current) setBusy(''); }
  }
  async function ignore() {
    if (lock.current || uncertainOperation) return;
    lock.current = true; setBusy('ignore');
    try {
      await ignoreJobMail(suggestion.id, suggestion.version);
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
      if (live.current) onClose();
    } catch (err) { if (live.current) setError(mailErrorText(err, '忽略操作未确认成功，请刷新建议状态。')); }
    finally { lock.current = false; if (live.current) { setBusy(''); setIgnoreOpen(false); } }
  }
  function fieldHint(key: keyof JobMailFields) {
    const issue = fieldErrors[key];
    const edited = Object.prototype.hasOwnProperty.call(fields, key);
    const proposed = suggestion.proposed_fields[key];
    const inherited = !edited && (proposed === undefined || proposed === null || proposed === '') && targetEvent?.[key] !== undefined;
    const text = issue ?? (edited ? '已编辑' : inherited ? '沿用现有记录' : suggestion.field_evidence[key] !== undefined ? '已从邮件预填，可直接修改' : '需核实，可直接修改');
    return <small id={`mail-${key}-hint`} className={issue ? styles.fieldError : styles.muted}>{text}</small>;
  }
  return <div className={`${styles.section} ${styles.review}`}>
    <details className={styles.disclosure} open={manualOnly} aria-label="邮件原文依据">
      <summary><span>查看邮件原文</span><span className={styles.sourceSubject}>{suggestion.evidence?.subject ?? '原文不可用'}</span></summary>
      <div className={styles.section}>
        <dl className={styles.details}>
          <dt>发件人</dt><dd>{suggestion.evidence?.sender || '未提供'}</dd>
          <dt>接收时间</dt><dd>{displayMailTime(suggestion.evidence?.received_at)}</dd><dt>建议版本</dt><dd>{suggestion.version}</dd>
          <dt>时间含义</dt><dd>{suggestion.time_mode === 'fixed' && !suggestion.proposed_fields.scheduled_at ? '未确定／需核对原文' : { fixed: '固定时刻', fixed_time: '固定时刻', deadline: '截止', window: '时间窗口', unknown: '待补充', unspecified: '时间未定' }[suggestion.time_mode] ?? suggestion.time_mode}</dd>
        </dl>
        <p className={styles.muted}>正文和发件信息仅作核对依据，手动提供的发件身份未经验证。不会执行邮件指令、加载 HTML 或访问外链。</p>
        {sourceAvailable && <pre className={styles.evidence}>{suggestion.evidence?.snippet}</pre>}
        {suggestion.evidence?.truncated && <Alert type="warning" message="原文片段已截断，未覆盖整封邮件；请核对遗漏内容" />}
        {!!Object.keys(suggestion.field_evidence).length && <details className={styles.disclosure}><summary>查看字段提取依据</summary><MailRecord value={suggestion.field_evidence} /></details>}
        <p className={styles.muted}>没有可靠原邮件直达链接时，请按主题、发件人和时间回邮箱查找。</p>
      </div>
    </details>
    {!sourceAvailable && <Alert type="info" message="原文已清理，无法继续核对原文" />}
    <section className={styles.reviewForm} aria-label="建议编辑与确认">
      <div className={styles.sectionHeader}>
        <h3 className={styles.title}>{mailActionLabel(suggestion.action)}</h3>
        {canReview && <span className={styles.status}>预填内容可直接编辑</span>}
      </div>
      {suggestion.reason && <p className={styles.muted}>{suggestion.reason}</p>}
      {receipt ? <>
        <Alert type="success" showIcon message="已确认写入" description={`事件 #${receipt.application_event_id} · ${displayMailTime(receipt.confirmed_at)}`} />
        <MailRecord value={receipt.after} />
        <p className={styles.muted}>操作回执：{receipt.operation_id}</p>
        <div><Button type="primary" onClick={() => { onClose(); onOpenRecord(Number(receipt.after.application_id)); }}>查看记录</Button></div>
        <details className={styles.disclosure}><summary>查看处理历史与原记录</summary><MailRecord value={receipt.before} /></details>
      </> : readOnly ? <Alert type="info" message={suggestion.status === 'ignored' ? '此建议已忽略' : '此建议已处理，请刷新查看回执'} /> : manualOnly ? <>
        <Alert type="warning" showIcon message="此类建议需要人工处理" description="第一批不自动应用截止、时间窗口、时间未定、Offer、取消、复杂改期或阶段变化。不会虚构开始时间和时长。" />
        <MailRecord value={suggestion.proposed_fields as Record<string, unknown>} />
        <div className={styles.actions}><Button onClick={() => { onClose(); onNavigate('calendar'); }}>前往日历手动处理</Button><Button onClick={() => { onClose(); onNavigate('offers'); }}>前往 Offer</Button></div>
      </> : <>
        {appsQuery.isError && <Alert type="error" message="投递列表读取失败，不能确认目标" action={<Button onClick={() => void appsQuery.refetch()}>重试</Button>} />}
        <label className={styles.field}>目标投递
          <select className={styles.select} aria-label="目标投递" aria-describedby="mail-target-hint" value={applicationId ?? ''} disabled={blocked} onChange={(e) => { invalidatePreview(); setApplicationId(e.target.value ? Number(e.target.value) : undefined); setTargetEventId(undefined); }}>
            <option value="">请选择投递</option>{applications.map((app) => <option key={app.id} value={app.id}>{app.company_name} · {app.position_name}（#{app.id}）</option>)}
          </select>
          <small id="mail-target-hint" className={styles.muted}>{suggestion.application_candidates.length === 1 ? '已预选唯一候选，请核对公司和岗位。' : '请选择本次安排对应的投递。'} 不会新建投递或改变阶段。</small>
        </label>
        {suggestion.action === 'update_event' && <label className={styles.field}>更新同一个现有事件
          <select className={styles.select} aria-label="目标事件" value={targetEventId ?? ''} disabled={blocked || !applicationId || eventsQuery.isError} onChange={(e) => { invalidatePreview(); setTargetEventId(e.target.value ? Number(e.target.value) : undefined); }}>
            <option value="">请选择该投递已有事件</option>{(eventsQuery.data ?? []).map((event) => <option key={event.id} value={event.id}>#{event.id} · {displayMailField('event_type', event.event_type)} · {displayMailTime(event.scheduled_at)}</option>)}
          </select>
        </label>}
        {eventsQuery.isError && suggestion.action === 'update_event' && <Alert type="error" message="事件读取失败，请刷新后重试" />}
        <div className={styles.fieldGrid}>
          <label className={styles.field}>事件类型
            <select className={styles.select} aria-label="事件类型" aria-invalid={!!fieldErrors.event_type} aria-describedby="mail-event_type-hint" value={effectiveFields.event_type ?? ''} disabled={blocked} onChange={(e) => change('event_type', e.target.value as JobMailFields['event_type'])}><option value="">请选择</option><option value="interview">面试</option><option value="written_test">笔试／测评</option><option value="custom">自定义</option></select>
            {fieldHint('event_type')}
          </label>
          <label className={styles.field}>时长（分钟）
            <input className={styles.input} aria-label="时长" aria-invalid={!!fieldErrors.duration_minutes} aria-describedby="mail-duration_minutes-hint" type="number" min={1} step={1} value={effectiveFields.duration_minutes ?? ''} disabled={blocked} onChange={(e) => change('duration_minutes', e.target.value ? Number(e.target.value) : undefined)} />
            {fieldHint('duration_minutes')}
          </label>
          <label className={styles.field}>开始时间（含时区）
            <input className={styles.input} aria-label="开始时间" aria-invalid={!!fieldErrors.scheduled_at} aria-describedby="mail-scheduled_at-hint mail-time-format" placeholder="2026-10-15T15:00:00+08:00" value={effectiveFields.scheduled_at ?? ''} disabled={blocked} onChange={(e) => change('scheduled_at', e.target.value)} />
            {fieldHint('scheduled_at')}
          </label>
          <label className={styles.field}>地点／会议链接（纯文本）
            <input className={styles.input} aria-label="地点" value={effectiveFields.location ?? ''} disabled={blocked} onChange={(e) => change('location', e.target.value)} />
          </label>
          {(effectiveFields.event_type === 'written_test' || !!effectiveFields.subtype) && <label className={styles.field}>子类型<input className={styles.input} aria-label="笔试子类型" placeholder="测评为 assessment" value={effectiveFields.subtype ?? ''} disabled={blocked} onChange={(e) => change('subtype', e.target.value)} /><small className={styles.muted}>测评 assessment 仅适用于笔试类型。</small></label>}
        </div>
        <p id="mail-time-format" className={styles.muted}>时间保留原始时区，格式示例：2026-10-15T15:00:00+08:00。缺失的时间和时长需核实后补充。</p>
        <label className={styles.field}>备注<textarea className={styles.textarea} aria-label="备注" rows={2} value={effectiveFields.notes ?? ''} disabled={blocked} onChange={(e) => change('notes', e.target.value)} /></label>
        <details className={styles.disclosure}><summary>轮次、标签与提醒（可选）</summary><div className={styles.fieldGrid}>
          <label className={styles.field}>轮次（0 表示未确定）<input className={styles.input} aria-label="轮次" type="number" min={0} max={100} step={1} value={effectiveFields.round ?? ''} disabled={blocked} onChange={(e) => change('round', e.target.value ? Number(e.target.value) : undefined)} /></label>
          <label className={styles.field}>标签（逗号分隔）<input className={styles.input} aria-label="标签" value={effectiveFields.tags?.join(', ') ?? ''} disabled={blocked} onChange={(e) => change('tags', e.target.value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))} /></label>
          <label className={`${styles.field} ${styles.fullWidth}`}>提醒时间（含时区；清空表示移除）<input className={styles.input} aria-label="提醒时间" placeholder="2026-10-15T14:30:00+08:00" value={effectiveFields.remind_at ?? ''} disabled={blocked} onChange={(e) => change('remind_at', e.target.value || null)} /></label>
        </div></details>
        <div className={styles.previewAction}>
          <Button type={preview ? 'default' : 'primary'} onClick={() => void preparePreview()} loading={busy === 'preview'} disabled={blocked || !selectedApp || !canReview || !!preview}>预览最终变更</Button>
          <p className={styles.muted}>{preview ? '已生成预览；修改任一字段后需重新预览。' : '预览不会写入，核对摘要后再确认。'}</p>
        </div>
      </>}
    </section>
    {preview && !receipt && <section className={styles.preview} aria-label="最终变更预览">
      <div className={styles.sectionHeader}><h3 ref={previewHeading} tabIndex={-1} className={styles.title}>最终确认摘要</h3><span className={styles.status}>尚未写入</span></div>
      <div className={styles.targetSummary}><strong>{preview.application_snapshot.company_name} · {preview.application_snapshot.position_name}</strong><span className={styles.muted}>投递 #{preview.application_id} · {preview.target_event_id ? `更新事件 #${preview.target_event_id}` : '新增一个事件'}</span></div>
      <FinalFields preview={preview} />
      {preview.warnings.map((warning, index) => <Alert key={index} type="warning" message={warning} />)}
      <details className={styles.disclosure}><summary>查看完整变更与目标快照</summary><div className={styles.section}>
        <h4 className={styles.subheading}>目标投递当前快照</h4><MailRecord value={preview.application_snapshot} kind="application" />
        <div className={styles.twoColumns}><section className={styles.section}><h4 className={styles.subheading}>目标事件当前快照</h4><MailRecord value={preview.before} /></section><section className={styles.section}><h4 className={styles.subheading}>确认后最终字段</h4><MailRecord value={preview.after} /></section></div>
      </div></details>
      <p className={styles.muted}>预览有效至 {displayMailTime(preview.expires_at)}。目标、建议或邮箱范围变化后需重新审阅。</p>
      <div className={styles.confirmAction}>
        <p className={styles.muted}>确认仅写入以上安排，不改变投递阶段，也不接受邮件中的其他指令。</p>
        <Button type="primary" disabled={blocked} loading={busy === 'confirm'} onClick={() => void apply()}>{suggestion.action === 'update_event' ? '确认更新此事件' : '确认加入日程'}</Button>
      </div>
    </section>}
    {uncertainOperation && !receipt && <Alert type="warning" showIcon message="有一笔提交结果待核对" description={`操作 ${uncertainOperation}。必须先查回执，当前不能发起新的写入。`} action={<Button loading={busy === 'receipt'} onClick={() => void recover()}>核对回执</Button>} />}
    {error && <Alert type="error" showIcon message={error} />}
    <div className={styles.footer}>
      {ignoreOpen ? <Alert type="warning" message="忽略本次建议？" description="只处理此建议，不取消日程或结束投递。" action={<div className={styles.actions}><Button danger disabled={blocked} onClick={() => void ignore()}>确认忽略</Button><Button disabled={blocked} onClick={() => setIgnoreOpen(false)}>返回</Button></div>} /> : <div className={styles.actions}>
        <Button disabled={!!busy} onClick={onClose}>{readOnly ? '关闭' : '暂不处理，保留待核对'}</Button>
        {!readOnly && <Button danger disabled={blocked} onClick={() => setIgnoreOpen(true)}>忽略本次建议</Button>}
      </div>}
      {!readOnly && <p className={styles.muted}>关闭不会写入；建议仍保留，未提交的编辑不保存。</p>}
    </div>
  </div>;
}

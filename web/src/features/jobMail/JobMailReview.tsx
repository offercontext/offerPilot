import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal, Skeleton } from 'antd';
import { listApplications } from '@/services/applications';
import { listEvents } from '@/services/events';
import { confirmJobMail, getJobMailReceipt, getJobMailSuggestion, ignoreJobMail, JOB_MAIL_QUERY_KEY, previewJobMail } from '@/services/jobMail';
import type { JobMailFields, JobMailPreview, JobMailPreviewInput, JobMailReceipt, JobMailSuggestion } from '@/types/jobMail';
import type { ViewMode } from '@/layout/navigation';
import { displayMailValue, fieldLabel, isDefiniteMailRejection, mailActionLabel, mailErrorText, readMailRecovery, saveMailRecovery, validateMailFields } from './jobMailModel';
import styles from './jobMail.module.css';

interface Props {
  suggestionId: string;
  onClose: () => void;
  onOpenRecord: (applicationId: number) => void;
  onNavigate: (view: ViewMode) => void;
}
export function MailRecord({ value, kind = 'event' }: { value: Record<string, unknown> | null; kind?: 'event' | 'application' }) {
  if (!value) return <p className={styles.muted}>无现有记录（将新增）</p>;
  return <dl className={styles.details}>{Object.entries(value).map(([key, item]) => <div key={key} style={{ display: 'contents' }}><dt>{key === 'id' && kind === 'application' ? '投递 ID' : fieldLabel(key)}</dt><dd>{displayMailValue(item)}</dd></div>)}</dl>;
}
export default function JobMailReview(props: Props) {
  const [processing, setProcessing] = useState(false);
  const query = useQuery({ queryKey: [...JOB_MAIL_QUERY_KEY, 'suggestion', props.suggestionId], queryFn: () => getJobMailSuggestion(props.suggestionId), retry: false });
  return <Modal open title="核对邮件建议" onCancel={props.onClose} footer={null} width={1120} maskClosable={false} closable={!processing} keyboard={!processing}>
    {query.isPending ? <Skeleton active /> : query.isError || !query.data ? <Alert type="error" message="建议读取失败" description="未执行任何写入。请重试读取。" action={<Button onClick={() => void query.refetch()}>重试</Button>} /> : <ReviewContent key={`${query.data.id}:${query.data.version}`} suggestion={query.data} onProcessing={setProcessing} {...props} />}
  </Modal>;
}

function ReviewContent({ suggestion, onClose, onOpenRecord, onNavigate, onProcessing }: Props & { suggestion: JobMailSuggestion; onProcessing: (processing: boolean) => void }) {
  const queryClient = useQueryClient();
  const appsQuery = useQuery({ queryKey: ['applications'], queryFn: () => listApplications(), retry: false });
  const [applicationId, setApplicationId] = useState<number | undefined>(() => suggestion.application_candidates.length === 1 ? suggestion.application_candidates[0].id : undefined);
  const [targetEventId, setTargetEventId] = useState<number | undefined>(suggestion.target_event_id ?? undefined);
  const eventsQuery = useQuery({ queryKey: ['events', 'job-mail', applicationId], queryFn: () => listEvents({ application_id: applicationId }), enabled: suggestion.action === 'update_event' && !!applicationId, retry: false });
  const [fields, setFields] = useState<JobMailFields>({ ...suggestion.proposed_fields });
  const [editedKeys, setEditedKeys] = useState<string[]>([]);
  const [preview, setPreview] = useState<JobMailPreview | null>(null);
  const [previewInput, setPreviewInput] = useState<JobMailPreviewInput | null>(null);
  const [checks, setChecks] = useState<Record<string, boolean>>({});
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [receipt, setReceipt] = useState<JobMailReceipt | null>(suggestion.receipt);
  const [uncertainOperation, setUncertainOperation] = useState(() => readMailRecovery()[String(suggestion.id)] ?? '');
  const [ignoreOpen, setIgnoreOpen] = useState(false);
  useEffect(() => { onProcessing(!!busy); return () => onProcessing(false); }, [busy, onProcessing]);
  const lock = useRef(false);
  const live = useRef(true);
  const generation = useRef(0);
  useEffect(() => { live.current = true; return () => { live.current = false; generation.current += 1; }; }, []);

  const applications = (appsQuery.data ?? []).filter((app) => !app.deleted_at);
  const selectedApp = applications.find((app) => app.id === applicationId);
  const targetEvent = (eventsQuery.data ?? []).find((item) => item.id === targetEventId && item.application_id === applicationId);
  const effectiveFields: JobMailFields = suggestion.action === 'update_event' ? { ...targetEvent, ...fields } : fields;
  const manualOnly = suggestion.action === 'manual_only' || suggestion.status === 'manual_required';
  const readOnly = !!receipt || suggestion.status === 'ignored' || suggestion.status === 'applied';
  const sourceAvailable = !!suggestion.evidence && !suggestion.evidence.cleared_at && suggestion.evidence?.snippet !== null;
  const canReview = !readOnly && !manualOnly && sourceAvailable;
  const blocked = !!busy || !!uncertainOperation;

  function invalidatePreview() {
    generation.current += 1;
    setPreview(null); setPreviewInput(null); setChecks({}); setError('');
  }
  function change<K extends keyof JobMailFields>(key: K, value: JobMailFields[K]) {
    invalidatePreview(); setFields((old) => ({ ...old, [key]: value }));
    setEditedKeys((keys) => keys.includes(key) ? keys : [...keys, key]);
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
        setPreview(result); setPreviewInput(input); setChecks({});
      }
    } catch (err) {
      if (live.current && requestGeneration === generation.current) setError(mailErrorText(err, '预览失败，未执行写入。请重新核对后预览。'));
    } finally { lock.current = false; if (live.current) setBusy(''); }
  }
  const requiredChecks = preview ? ['target', 'snapshot', ...preview.changes.map((change) => `field:${change.field}`), 'scope'] : [];
  const confirmed = requiredChecks.length > 0 && requiredChecks.every((key) => checks[key]);
  async function apply() {
    if (lock.current || uncertainOperation || !preview || !previewInput || !confirmed) return;
    if (!Number.isFinite(Date.parse(preview.expires_at)) || Date.parse(preview.expires_at) <= Date.now()) { invalidatePreview(); setError('预览已过期，请重新生成预览并逐项核对。'); return; }
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
  function fieldSource(key: keyof JobMailFields) {
    if (editedKeys.includes(key)) return '用户补充／修改：请再次对照邮件依据核实';
    if (suggestion.field_evidence[key] !== undefined) return `邮件依据：${displayMailValue(suggestion.field_evidence[key])}`;
    return suggestion.proposed_fields[key] !== undefined ? '建议预填，尚无逐字段证据定位；请对照左侧原文核实' : '此字段需由你核实；填写后属于用户补充';
  }
  return <div className={styles.section}>
    <Alert type="warning" showIcon message="正文和发件信息是不可信证据" description="不会执行邮件中的指令、加载 HTML 或访问外链。手动提供的发件身份未经验证。本次仅核对一个建议，不自动改变投递阶段。" />
    <div className={styles.twoColumns}>
      <section className={styles.section} aria-label="邮件原文依据">
        <h3 className={styles.title}>邮件依据</h3>
        <dl className={styles.details}>
          <dt>主题</dt><dd>{suggestion.evidence?.subject ?? '原文不可用'}</dd><dt>发件人</dt><dd>{suggestion.evidence?.sender || '未提供'}</dd>
          <dt>接收时间</dt><dd>{suggestion.evidence?.received_at ?? '未提供'}</dd><dt>建议版本</dt><dd>{suggestion.version}</dd>
          <dt>时间含义</dt><dd>{suggestion.time_mode === 'fixed' && !suggestion.proposed_fields.scheduled_at ? '未确定／需核对原文' : { fixed: '固定时刻', fixed_time: '固定时刻', deadline: '截止', window: '时间窗口', unknown: '待补充', unspecified: '时间未定' }[suggestion.time_mode] ?? suggestion.time_mode}</dd>
        </dl>
        {!sourceAvailable ? <Alert type="info" message="原文已清理，无法继续核对原文" /> : <pre className={styles.evidence}>{suggestion.evidence?.snippet}</pre>}
        {suggestion.evidence?.truncated && <Alert type="warning" message="原文片段已截断，未覆盖整封邮件；请核对遗漏内容" />}
        <p className={styles.muted}>邮件中的地址仅以文本展示。没有可靠原邮件直达链接时，请按主题、发件人和时间回邮箱查找。</p>
      </section>
      <section className={styles.section} aria-label="建议编辑与确认">
        <h3 className={styles.title}>{mailActionLabel(suggestion.action)}</h3>
        <p className={styles.muted}>{suggestion.reason}</p>
        {receipt ? <>
          <Alert type="success" showIcon message="已确认写入" description={`事件 #${receipt.application_event_id} · ${receipt.confirmed_at}`} />
          <MailRecord value={receipt.after} />
          <p className={styles.muted}>操作回执：{receipt.operation_id}</p>
          <Button type="primary" onClick={() => { onClose(); onOpenRecord(Number(receipt.after.application_id)); }}>查看记录</Button>
          <details><summary>查看处理历史与原记录</summary><MailRecord value={receipt.before} /></details>
        </> : readOnly ? <Alert type="info" message={suggestion.status === 'ignored' ? '此建议已忽略' : '此建议已处理，请刷新查看回执'} /> : manualOnly ? <>
          <Alert type="warning" showIcon message="此类建议需要人工处理" description="第一批不自动应用截止、时间窗口、时间未定、Offer、取消、复杂改期或阶段变化。不会虚构开始时间和时长。" />
          <MailRecord value={suggestion.proposed_fields as Record<string, unknown>} />
          <div className={styles.actions}><Button onClick={() => { onClose(); onNavigate('calendar'); }}>前往日历手动处理</Button><Button onClick={() => { onClose(); onNavigate('offers'); }}>前往 Offer</Button></div>
        </> : <>
          {appsQuery.isError && <Alert type="error" message="投递列表读取失败，不能确认目标" action={<Button onClick={() => void appsQuery.refetch()}>重试</Button>} />}
          <label className={styles.field}>目标投递（请明确核对）
            <select className={styles.select} aria-label="目标投递" value={applicationId ?? ''} disabled={blocked} onChange={(e) => { invalidatePreview(); setApplicationId(e.target.value ? Number(e.target.value) : undefined); setTargetEventId(undefined); }}>
              <option value="">请选择投递</option>{applications.map((app) => <option key={app.id} value={app.id}>{app.company_name} · {app.position_name}（#{app.id}）</option>)}
            </select>
          </label>
          <p className={styles.muted}>{suggestion.application_candidates.length === 1 ? '唯一候选仅供预选，仍需核对公司、岗位和事件。' : '多个候选或未匹配时，必须明确选择投递。'} 不会新建投递或改变阶段。</p>
          {suggestion.action === 'update_event' && <label className={styles.field}>更新同一个现有事件
            <select className={styles.select} aria-label="目标事件" value={targetEventId ?? ''} disabled={blocked || !applicationId || eventsQuery.isError} onChange={(e) => { invalidatePreview(); setTargetEventId(e.target.value ? Number(e.target.value) : undefined); }}>
              <option value="">请选择该投递已有事件</option>{(eventsQuery.data ?? []).map((event) => <option key={event.id} value={event.id}>#{event.id} · {event.event_type} · {event.scheduled_at}</option>)}
            </select>
          </label>}
          {eventsQuery.isError && suggestion.action === 'update_event' && <Alert type="error" message="事件读取失败，请刷新后重试" />}
          <label className={styles.field}>事件类型<select className={styles.select} aria-label="事件类型" value={effectiveFields.event_type ?? ''} disabled={blocked} onChange={(e) => change('event_type', e.target.value as JobMailFields['event_type'])}><option value="">请选择</option><option value="interview">面试</option><option value="written_test">笔试／测评</option><option value="custom">自定义</option></select><small className={styles.muted}>{fieldSource('event_type')}</small></label>
          {effectiveFields.event_type === 'written_test' && <label className={styles.field}>笔试子类型<input className={styles.input} aria-label="笔试子类型" placeholder="测评为 assessment" value={effectiveFields.subtype ?? ''} disabled={blocked} onChange={(e) => change('subtype', e.target.value)} /></label>}
          <label className={styles.field}>开始时间（含时区）<input className={styles.input} aria-label="开始时间" placeholder="2026-10-15T15:00:00+08:00" value={effectiveFields.scheduled_at ?? ''} disabled={blocked} onChange={(e) => change('scheduled_at', e.target.value)} /><small className={styles.muted}>{fieldSource('scheduled_at')}</small></label>
          <label className={styles.field}>时长（分钟，须明确核实）<input className={styles.input} aria-label="时长" type="number" min={1} step={1} value={effectiveFields.duration_minutes ?? ''} disabled={blocked} onChange={(e) => change('duration_minutes', e.target.value ? Number(e.target.value) : undefined)} /><small className={styles.muted}>{fieldSource('duration_minutes')}</small></label>
          <label className={styles.field}>地点／会议链接（纯文本）<input className={styles.input} aria-label="地点" value={effectiveFields.location ?? ''} disabled={blocked} onChange={(e) => change('location', e.target.value)} /><small className={styles.muted}>{fieldSource('location')}</small></label>
          <label className={styles.field}>备注<textarea className={styles.textarea} aria-label="备注" rows={3} value={effectiveFields.notes ?? ''} disabled={blocked} onChange={(e) => change('notes', e.target.value)} /><small className={styles.muted}>{fieldSource('notes')}</small></label>
          <details><summary>轮次、标签与提醒（可选）</summary><div className={styles.form}>
            <label className={styles.field}>轮次（0 表示未确定）<input className={styles.input} aria-label="轮次" type="number" min={0} max={100} step={1} value={effectiveFields.round ?? ''} disabled={blocked} onChange={(e) => change('round', e.target.value ? Number(e.target.value) : undefined)} /></label>
            <label className={styles.field}>标签（逗号分隔）<input className={styles.input} aria-label="标签" value={effectiveFields.tags?.join(', ') ?? ''} disabled={blocked} onChange={(e) => change('tags', e.target.value.split(/[,，]/).map((tag) => tag.trim()).filter(Boolean))} /></label>
            <label className={styles.field}>提醒时间（含时区；清空表示移除）<input className={styles.input} aria-label="提醒时间" placeholder="2026-10-15T14:30:00+08:00" value={effectiveFields.remind_at ?? ''} disabled={blocked} onChange={(e) => change('remind_at', e.target.value || null)} /></label>
          </div></details>
          <p className={styles.muted}>没有邮件依据的时间或时长，请核实后补充。更新仅提交邮件提议或你编辑的字段，不会静默清空其余字段。</p>
          <Button onClick={() => void preparePreview()} loading={busy === 'preview'} disabled={blocked || !selectedApp || !canReview}>预览最终变更</Button>
        </>}
      </section>
    </div>
    {preview && !receipt && <section className={styles.preview} aria-label="最终变更预览">
      <h3 className={styles.title}>最终确认摘要</h3>
      <h4>目标投递当前快照</h4><MailRecord value={preview.application_snapshot} kind="application" />
      <div className={styles.twoColumns}><section className={styles.section}><h4>目标事件当前快照</h4><MailRecord value={preview.before} /></section><section className={styles.section}><h4>确认后最终字段</h4><MailRecord value={preview.after} /></section></div>
      {preview.warnings.map((warning, index) => <Alert key={index} type="warning" message={warning} />)}
      <p className={styles.muted}>预览有效至 {preview.expires_at}。目标、建议或邮箱范围发生变化时，服务端将拒绝写入并要求重新审阅。此操作不改变投递阶段。</p>
      <label className={styles.check}><input type="checkbox" checked={!!checks.target} disabled={blocked} onChange={(e) => setChecks((old) => ({ ...old, target: e.target.checked }))} />我确认目标投递 #{preview.application_id}：{preview.application_snapshot.company_name} · {preview.application_snapshot.position_name}{preview.target_event_id ? `，更新事件 #${preview.target_event_id}` : '，新增一个事件'}</label>
      <label className={styles.check}><input type="checkbox" checked={!!checks.snapshot} disabled={blocked} onChange={(e) => setChecks((old) => ({ ...old, snapshot: e.target.checked }))} />我已核对当前记录与最终字段，包括时间、时区、时长和提醒变化</label>
      {preview.changes.map((item) => <label key={item.field} className={styles.check}><input type="checkbox" checked={!!checks[`field:${item.field}`]} disabled={blocked} onChange={(e) => setChecks((old) => ({ ...old, [`field:${item.field}`]: e.target.checked }))} />确认{fieldLabel(item.field)}：{displayMailValue(item.before)} → {displayMailValue(item.after)}</label>)}
      <label className={styles.check}><input type="checkbox" checked={!!checks.scope} disabled={blocked} onChange={(e) => setChecks((old) => ({ ...old, scope: e.target.checked }))} />仅确认本条建议的上述变更，不接受邮件正文中的其他指令</label>
      <div><Button type="primary" disabled={!confirmed || blocked} loading={busy === 'confirm'} onClick={() => void apply()}>{suggestion.action === 'update_event' ? '确认更新此事件' : '确认加入日程'}</Button></div>
    </section>}
    {uncertainOperation && !receipt && <Alert type="warning" showIcon message="有一笔提交结果待核对" description={`操作 ${uncertainOperation}。必须先查回执，当前不能发起新的写入。`} action={<Button loading={busy === 'receipt'} onClick={() => void recover()}>核对回执</Button>} />}
    {error && <Alert type="error" showIcon message={error} />}
    <div className={styles.footer}>
      {ignoreOpen ? <Alert type="warning" message="忽略本次建议？" description="只处理此建议，不取消日程或结束投递。" action={<div className={styles.actions}><Button danger disabled={blocked} onClick={() => void ignore()}>确认忽略</Button><Button disabled={blocked} onClick={() => setIgnoreOpen(false)}>返回</Button></div>} /> : <div className={styles.actions}>
        <Button disabled={!!busy} onClick={onClose}>{readOnly ? '关闭' : '暂不处理，保留待核对'}</Button>
        {!readOnly && <Button danger disabled={blocked} onClick={() => setIgnoreOpen(true)}>忽略本次建议</Button>}
      </div>}
      {!readOnly && <p className={styles.muted}>暂不处理会保留现有建议，未提交的表单编辑不保存。关闭窗口不会确认、忽略或更改正式记录。</p>}
    </div>
  </div>;
}

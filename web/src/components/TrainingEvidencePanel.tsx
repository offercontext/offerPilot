import { useEffect, useRef, useState } from 'react';
import { t, useLocale } from '@/i18n';
import { changeTrainingEvidence, getTrainingEvidence, TrainingEvidenceError } from '@/services/trainingEvidence';
import type { TrainingAssistance, TrainingEvidenceCommand, TrainingEvidenceDraft, TrainingEvidenceRecord, TrainingEvidenceState, TrainingScenario } from '@/types/trainingEvidence';
import styles from './TrainingEvidencePanel.module.css';

const assistanceLabel = (value: TrainingAssistance) => ({
  unknown: t('未记录辅助情况'), independent: t('独立作答'), hinted: t('提示后作答'), reference: t('看过参考答案后作答'),
})[value];
const scenarioLabel = (value: TrainingScenario) => ({
  original: t('原练习回答'), retry_timeout: t('写入成功但响应超时'), concurrent_retry: t('并发重复请求'),
})[value];

const scenarioPrompt = (value: TrainingScenario) => ({
  original: t('回看这次已保存的练习回答，并如实标注作答时的帮助情况。'),
  retry_timeout: t('投递已成功写入，但客户端等待响应超时。用户点击重试：如何判断是否已成功，并避免重复写入？请说明依据和恢复步骤。'),
  concurrent_retry: t('同一份投递请求被两个客户端同时重试。如何保证只写入一次，并让两个请求都获得可核对的结果？请说明唯一约束和回执处理。'),
})[value];
function recommendationCopy(state: TrainingEvidenceState) {
  const records = state.recommendation?.evidence_record_ids.map((id) => state.records.find((record) => record.id === id && !record.withdrawn)).filter((record): record is TrainingEvidenceRecord => Boolean(record)) ?? [];
  const latest = records[records.length - 1];
  const previous = records[records.length - 2];
  return {
    reason: !latest ? t('没有可用的训练依据。') : latest.assistance === 'unknown'
      ? t('记录 #{id} 的帮助情况未知，不能由回答质量倒推。建议换情境作答并如实标注帮助情况。', { id: latest.id })
      : t('你将记录 #{id} 标注为“{assistance}”。建议换一个情境独立尝试，再核对推理依据；此标注不证明已经掌握。', { id: latest.id, assistance: assistanceLabel(latest.assistance) }),
    comparison: previous && latest ? t('记录 #{previous} 标注为“{before}”，记录 #{latest} 标注为“{after}”。这是用户标注的作答条件变化，不是能力评分。', { previous: previous.id, before: assistanceLabel(previous.assistance), latest: latest.id, after: assistanceLabel(latest.assistance) }) : t('目前只有一条可用记录，尚不能比较变化。'),
  };
}

export default function TrainingEvidencePanel({ planId, originalAnswer, draft, onDraftChange }: {
  planId: number;
  originalAnswer: string;
  draft?: TrainingEvidenceDraft;
  onDraftChange: (draft: TrainingEvidenceDraft | null) => boolean;
}) {
  useLocale();
  const [opened, setOpened] = useState(Boolean(draft));
  const [state, setState] = useState<TrainingEvidenceState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<'load' | 'conflict' | 'unknown' | null>(null);
  const [pending, setPending] = useState<TrainingEvidenceCommand | null>(draft?.command ?? null);
  const [scenario, setScenario] = useState<TrainingScenario>(draft?.scenario ?? 'original');
  const [answer, setAnswer] = useState(draft?.answer ?? originalAnswer);
  const [assistance, setAssistance] = useState<TrainingAssistance>(draft?.assistance ?? 'unknown');
  const [editing, setEditing] = useState<number | null>(draft?.editingId ?? null);
  const [withdrawTarget, setWithdrawTarget] = useState<number | null>(null);
  const mounted = useRef(true);
  const generation = useRef(0);
  const inFlight = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current += 1; inFlight.current = false; };
  }, []);
  useEffect(() => {
    if (!pending && !busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [busy, pending]);

  const load = async () => {
    if (inFlight.current) return;
    const ticket = ++generation.current;
    setOpened(true); setBusy(true); inFlight.current = true;
    try {
      const next = await getTrainingEvidence(planId);
      if (!mounted.current || ticket !== generation.current) return;
      setState(next); setError(pending ? 'unknown' : null);
      if (!pending && (!next.available || (editing !== null && !next.records.some((record) => record.id === editing && !record.withdrawn)))) { clearForm(); }
    } catch {
      if (mounted.current && ticket === generation.current) { setState(null); setError('load'); }
    } finally {
      if (ticket === generation.current) {
        inFlight.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  };
  useEffect(() => {
    if (draft) void load();
    // An owned draft is an existing explicit choice; do not auto-load new plans.
    // The panel is keyed by owner scope and plan, so this is mount recovery only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const submit = async (command: TrainingEvidenceCommand) => {
    if (inFlight.current) return;
    const ticket = ++generation.current;
    if (!onDraftChange({ scenario, answer, assistance, editingId: editing, command })) { setError('conflict'); return; }
    inFlight.current = true; setBusy(true); setPending(command);
    try {
      const next = await changeTrainingEvidence(planId, command);
      if (!mounted.current || ticket !== generation.current) return;
      setState(next); setPending(null); setError(null); setEditing(null); setWithdrawTarget(null); onDraftChange(null);
      setScenario(command.action === 'withdraw' ? 'retry_timeout' : next.recommendation?.scenario_id ?? 'original');
      setAnswer(command.action === 'withdraw' ? '' : next.recommendation?.scenario_id === 'original' || !next.recommendation ? originalAnswer : '');
      setAssistance('unknown');
    } catch (cause) {
      const definitive = cause instanceof TrainingEvidenceError && (
        (cause.status === 422 && cause.code === 'training_evidence_invalid')
        || (cause.status === 404 && cause.code === 'training_evidence_not_found')
        || (cause.status === 409 && ['training_evidence_conflict', 'training_evidence_idempotency_conflict', 'training_evidence_source_unavailable'].includes(cause.code ?? ''))
      );
      if (!mounted.current || ticket !== generation.current) return;
      setError(definitive ? 'conflict' : 'unknown');
      if (definitive) { setPending(null); onDraftChange({ scenario, answer, assistance, editingId: editing, command: null }); }
    } finally {
      if (ticket === generation.current) {
        inFlight.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  };
  const identity = () => ({ expected_revision: state!.revision, idempotency_key: crypto.randomUUID() });
  const activeCount = state?.records.filter((record) => !record.withdrawn).length ?? 0;
  const atCapacity = activeCount >= 12;
  const frozen = busy || pending !== null || error === 'conflict' || error === 'load';
  const beginEdit = (record: TrainingEvidenceRecord) => {
    setEditing(record.id); onDraftChange({ scenario: record.scenario_id, answer: record.answer, assistance: record.assistance, editingId: record.id, command: null }); setScenario(record.scenario_id); setAnswer(record.answer); setAssistance(record.assistance); setWithdrawTarget(null);
  };
  const clearForm = () => { setEditing(null); setScenario('retry_timeout'); setAnswer(''); setAssistance('unknown'); onDraftChange(null); };
  const selectScenario = (value: TrainingScenario) => {
    onDraftChange({ scenario: value, answer: value === 'original' ? originalAnswer : '', assistance: 'unknown', editingId: null, command: null });
    setScenario(value); setAnswer(value === 'original' ? originalAnswer : ''); setAssistance('unknown'); setEditing(null);
  };

  return <section className={styles.panel} aria-label={t('训练证据')} aria-busy={busy}>
    <h4>{t('训练证据：幂等与重试')}</h4>
    <p>{t('来源：已完成练习 #{id}', { id: planId })}</p>
    <p>{t('仅在你选择这个技能并确认保存后，用于本计划的下一次练习；不会自动加入全局记忆或发送给 AI，也不表示已经掌握。')}</p>
    {!opened ? <button type="button" onClick={() => void load()}>{t('选择这个技能并查看记录')}</button> : <>
      <p>{t('过去未记录的辅助情况保持未知，不根据回答质量推断。')}</p>
      {error === 'load' ? <p role="alert">{t('训练记录读取失败，请重新读取。')}</p> : null}
      {error === 'conflict' ? <p role="alert">{t('记录或来源状态已变化，请重新读取后核对，未自动覆盖。')}</p> : null}
      {pending ? <div role="alert"><p>{t('保存结果待确认，输入已冻结。请使用原操作重试，不要重复新建记录。')}</p><button type="button" disabled={busy} onClick={() => void submit(pending)}>{t('使用原操作重试')}</button></div> : null}
      <button type="button" disabled={busy || pending !== null} onClick={() => void load()}>{t('重新读取')}</button>
      {state ? <>
        <p role="status">{t('有效训练记录：{used}/12；剩余容量 {remaining} 条。', { used: activeCount, remaining: Math.max(0, 12 - activeCount) })}</p>
        {state.available && atCapacity ? <p role="status">{t('已达到 12 条有效记录上限。可先撤回一条再新增；更正和撤回仍可用。')}</p> : null}
        {!state.available ? <p role="status">{t('来源或目标已变化，已暂停新增、修改和建议。仍可查看历史并撤回训练记录。')}</p> : null}
        {state.available && state.recommendation ? <aside className={styles.advice}>
          <strong>{t('下一次怎么练')}</strong>
          <p>{recommendationCopy(state).reason}</p><p>{recommendationCopy(state).comparison}</p><p>{scenarioPrompt(state.recommendation.scenario_id)}</p>
          <p>{t('依据记录：')}{state.recommendation.evidence_record_ids.map((id) => <a key={id} href={`#training-${planId}-${id}`}> #{id}</a>)}</p>
          <button type="button" disabled={frozen || atCapacity} onClick={() => selectScenario(state.recommendation!.scenario_id)}>{t('换个场景试一次')}</button>
        </aside> : null}
        <ul className={styles.records}>{state.records.map((record) => <li id={`training-${planId}-${record.id}`} key={record.id}>
          <strong>#{record.id} · {scenarioLabel(record.scenario_id)}</strong><p><time>{record.created_at}</time>{record.updated_at !== record.created_at ? <> · {t('已更正：')}{record.updated_at}</> : null}</p>
          {record.withdrawn ? <p>{t('已撤回，不再用于建议；训练记录中的回答已清除。')}</p> : <>
            <p>{t('用户记录的作答条件：')}{assistanceLabel(record.assistance)}</p><p className={styles.answer}>{record.answer}</p>
            <button type="button" disabled={frozen || !state.available} onClick={() => beginEdit(record)}>{t('更正记录')}</button>{' '}
            <button type="button" disabled={frozen} onClick={() => setWithdrawTarget(record.id)}>{t('撤回训练记录')}</button>
            {withdrawTarget === record.id ? <div role="group" aria-label={t('确认撤回训练记录')}><p>{t('撤回后清除这条训练记录的回答，不再用于建议。原计划的完成回答仍保留。')}</p><button type="button" disabled={frozen} onClick={() => void submit({ ...identity(), action: 'withdraw', record_id: record.id })}>{t('确认撤回训练记录')}</button>{' '}<button type="button" disabled={frozen} onClick={() => setWithdrawTarget(null)}>{t('取消')}</button></div> : null}
          </>}
        </li>)}</ul>
        {state.available ? <fieldset disabled={frozen || (editing === null && atCapacity)} className={styles.form}>
          <legend>{editing ? t('更正训练记录') : t('显式保存一次练习证据')}</legend>
          <label>{t('练习情境')}<select aria-label={t('练习情境')} value={scenario} disabled={editing !== null} onChange={(event) => selectScenario(event.target.value as TrainingScenario)}>{state.scenarios.map((item) => <option key={item.id} value={item.id}>{scenarioLabel(item.id)}</option>)}</select></label>
          <p>{scenarioPrompt(scenario)}</p>
          <label>{t('本次回答')}<textarea aria-label={t('本次回答')} value={answer} readOnly={scenario === 'original'} maxLength={8000} rows={5} onChange={(event) => { setAnswer(event.target.value); onDraftChange({ scenario, answer: event.target.value, assistance, editingId: editing, command: null }); }} /></label>
          {scenario === 'original' ? <small>{t('原回答仅引用已完成计划；这里可以更正辅助情况，不改写原回答。')}</small> : null}
          <label>{t('这次如何完成回答')}<select aria-label={t('这次如何完成回答')} value={assistance} onChange={(event) => { const value = event.target.value as TrainingAssistance; setAssistance(value); onDraftChange({ scenario, answer, assistance: value, editingId: editing, command: null }); }}>{(['unknown', 'independent', 'hinted', 'reference'] as const).map((value) => <option key={value} value={value}>{assistanceLabel(value)}</option>)}</select></label>
          <p>{t('以下保存是你的记录，不是 AI 对能力的判定。')}</p>
          <button type="button" disabled={!answer.trim() || (editing === null && atCapacity)} onClick={() => void submit(editing ? { ...identity(), action: 'correct', record_id: editing, answer, assistance } : { ...identity(), action: 'record', skill_id: 'idempotency_retry', scenario_id: scenario, answer, assistance })}>{editing ? t('确认保存更正') : t('确认保存训练证据')}</button>
          {editing ? <button type="button" onClick={clearForm}>{t('取消更正')}</button> : null}
        </fieldset> : null}
      </> : null}
    </>}
  </section>;
}

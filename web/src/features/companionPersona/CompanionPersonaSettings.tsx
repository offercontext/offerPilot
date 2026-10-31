import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Select, Space, Typography } from 'antd';
import axios from 'axios';
import { t, useLocale } from '@/i18n';
import { ACTIVITY_TEMPLATES, getPersona, getPersonaRevisions, mutatePersona, type PersonaMutation, type PersonaRevision, type PersonaSpec, type PersonaView } from '@/services/persona';
import { publishPersona } from './useCompanionName';
import { FIELD_LABELS, personaDifferences, validPersona } from './personaModel';
import styles from './persona.module.css';
import { readPendingPersonaMutation, retainPendingPersonaMutation } from './pendingPersonaMutation';
const PRESETS = { official: '官方默认', gentle: '温和简洁', concise: '冷静直接' };
const ACTIVITIES = { paper_boat: '纸小船', cloud_catalog: '云朵名字', paper_stars: '纸星星' };
const VERBOSITY = { brief: '简短', balanced: '均衡', detailed: '详细' };
export default function CompanionPersonaSettings() {
  useLocale();
  const submitting = useRef(false);
  const [current, setCurrent] = useState<PersonaView | null>(null);
  const [draft, setDraft] = useState<PersonaSpec | null>(null);
  const [history, setHistory] = useState<PersonaRevision[] | null>(null);
  const [restore, setRestore] = useState<number | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [refreshRequired, setRefreshRequired] = useState(false);
  const [pending, setPending] = useState<PersonaMutation | null>(null);
  async function load(keepDraft = false) {
    setBusy(true);
    try {
      const value = await getPersona(); setCurrent(value); publishPersona(value);
      if (!keepDraft) { setDraft(structuredClone(value.spec)); setRestore(null); }
      const retained = readPendingPersonaMutation(value.profile_id);
      setPending(retained);
      if (retained?.operation === 'update') setDraft(structuredClone(retained.spec));
      setRefreshRequired(false); setConfirmed(false); setConflict(false); setError(retained ? '尚未确认保存结果。请重试同一请求，不会重复创建版本。' : '');
    } catch { setError('无法读取伙伴人设，请重试。'); }
    finally { setBusy(false); }
  }
  useEffect(() => { void load(); }, []);
  async function loadHistory() {
    setBusy(true);
    try { setHistory((await getPersonaRevisions()).revisions); setError(''); }
    catch { setError('无法读取人设历史，请重试。'); }
    finally { setBusy(false); }
  }
  async function submit(command?: PersonaMutation) {
    if (!current || !draft || submitting.current || (!command && !confirmed)) return;
    submitting.current = true;
    const request: PersonaMutation = command ?? readPendingPersonaMutation(current.profile_id) ?? {
      profile_id: current.profile_id, expected_revision: current.revision,
      command_id: crypto.randomUUID(), confirmed: true,
      ...(restore === null ? { operation: 'update' as const, spec: draft } : { operation: 'restore' as const, target_revision: restore }),
    };
    retainPendingPersonaMutation(request); setPending(request); setBusy(true); setError('');
    try {
      await mutatePersona(request);
      // A replay receipt describes the original save, not necessarily the active
      // revision: another window may have saved since the response was lost.
      retainPendingPersonaMutation(null); setPending(null); setConfirmed(false); setRestore(null); setHistory(null);
      try {
        const active = await getPersona();
        setCurrent(active); setDraft(structuredClone(active.spec)); publishPersona(active);
        setRefreshRequired(false);
      } catch {
        setRefreshRequired(true);
        setError('保存已确认，暂时无法读取当前人设。请刷新，不要重复保存。');
      }
    } catch (cause) {
      const status = axios.isAxiosError(cause) ? cause.response?.status : undefined;
      if (status === 409) { retainPendingPersonaMutation(null); setPending(null); setConflict(true); setConfirmed(false); setError('人设已在其他窗口更新。草稿已保留，请读取最新版后重新核对差异。'); }
      else if (status && status >= 400 && status < 500) { retainPendingPersonaMutation(null); setPending(null); setConfirmed(false); setError('保存被拒绝，请检查字段或登录状态后重试。'); }
      else setError('尚未确认保存结果。请重试同一请求，不会重复创建版本。');
    } finally { submitting.current = false; setBusy(false); }
  }
  const disabled = busy || Boolean(pending) || conflict || refreshRequired;
  const differences = current && draft ? personaDifferences(current.spec, draft) : [];
  function edit<K extends keyof PersonaSpec>(key: K, value: PersonaSpec[K]) {
    if (draft) setDraft({ ...draft, [key]: value });
    setConfirmed(false); setRestore(null);
  }
  return <section className={styles.settings} aria-labelledby="companion-persona-title">
    <Typography.Title level={4} id="companion-persona-title">{t('伙伴人设')}</Typography.Title>
    <p>{t('Pilot 与日常窗口共用同一个伙伴。保存后下一轮生效，正在进行的任务保留原人设。不会修改记忆、已有活动、承诺、权限或通知预算。')}</p>
    {error && <Alert type="warning" message={t(error)} action={pending ? <Button disabled={busy} onClick={() => void submit(pending)}>{t('重试同一保存请求')}</Button> : refreshRequired ? <Button disabled={busy} onClick={() => void load()}>{t('刷新当前人设')}</Button> : conflict ? <Button disabled={busy} onClick={() => void load(true)}>{t('读取最新版并保留草稿')}</Button> : <Button disabled={busy} onClick={() => void load(Boolean(current))}>{t('重试')}</Button>} />}
    {!draft || !current ? <p>{t('正在读取伙伴人设…')}</p> : <>
      <Space wrap>{current.presets.map(preset => <Button key={preset.id} disabled={disabled} onClick={() => { setDraft(structuredClone(preset.spec)); setRestore(null); setConfirmed(false); }}>{t(PRESETS[preset.id])}</Button>)}</Space>
      <p>{t('预设仅加载为草稿，不会自动保存。')}</p>
      <div className={styles.fields}>
        <label><span>{t('伙伴名字')}</span><Input aria-label={t('伙伴名字')} value={draft.display_name} maxLength={24} disabled={disabled} onChange={event => edit('display_name', event.target.value)} /></label>
        <label><span>{t('性格标签')}</span><Input aria-label={t('性格标签')} value={draft.traits.join(', ')} disabled={disabled} placeholder={t('最多 5 个，以逗号分隔')} onChange={event => edit('traits', event.target.value ? event.target.value.split(/[,，]/).map(value => value.trim()) : [])} /></label>
        <label><span>{t('表达详略')}</span><Select aria-label={t('表达详略')} value={draft.verbosity} disabled={disabled} onChange={value => edit('verbosity', value)} options={Object.entries(VERBOSITY).map(([value, label]) => ({ value, label: t(label) }))} /></label>
        <label><span>{t('说话习惯')}</span><Input.TextArea aria-label={t('说话习惯')} value={draft.speaking_style} maxLength={200} disabled={disabled} onChange={event => edit('speaking_style', event.target.value)} /></label>
        <label><span>{t('伙伴兴趣')}</span><Input.TextArea aria-label={t('伙伴兴趣')} value={draft.interests_text} maxLength={300} disabled={disabled} onChange={event => edit('interests_text', event.target.value)} /></label>
      </div>
      <details><summary>{t('补充人设')}</summary><div className={styles.fields}>
        <label><span>{t('虚构背景')}</span><Input.TextArea aria-label={t('虚构背景')} value={draft.fictional_background} maxLength={500} disabled={disabled} onChange={event => edit('fictional_background', event.target.value)} /></label>
        <label><span>{t('补充表达说明')}</span><Input.TextArea aria-label={t('补充表达说明')} value={draft.additional_style_notes} maxLength={500} disabled={disabled} onChange={event => edit('additional_style_notes', event.target.value)} /></label>
      </div></details>
      <fieldset disabled={disabled} className={styles.activities}><legend>{t('可开展的虚拟活动')}</legend>
        <p>{t('兴趣文字只影响聊天，不会增加现实能力。以下活动可以全不选；只限制新活动，正在进行的活动和共同约定不变。')}</p>
        {ACTIVITY_TEMPLATES.map(id => <Checkbox key={id} disabled={disabled} checked={draft.enabled_activity_templates.includes(id)} onChange={event => edit('enabled_activity_templates', event.target.checked ? [...draft.enabled_activity_templates, id] : draft.enabled_activity_templates.filter(item => item !== id))}>{t(ACTIVITIES[id])}</Checkbox>)}
      </fieldset>
      <aside className={styles.preview} aria-label={t('静态表达预览')}>
        <strong>{t('表达示例，非实际模型生成效果')}</strong>
        <p>{draft.display_name || 'Haru'} · {t(VERBOSITY[draft.verbosity])}</p>
        <blockquote>{draft.verbosity === 'brief' ? t('我在，慢慢说。') : draft.verbosity === 'detailed' ? t('我在听。你可以慢慢讲，我会先理解你的问题，再把需要说明的部分说清楚。') : t('我在听，你想从哪件小事说起？')}</blockquote>
        <p>{t('自由文本仅作为表达资料；此示例不预测模型的实际回答。虚构背景不是共同经历或用户事实。')}</p>
      </aside>
      {differences.length > 0 && <div aria-label={t('人设变更对比')} className={styles.diff}>
        <strong>{t('人设变更对比')}</strong>
        {differences.map(key => <div key={key}><b>{t(FIELD_LABELS[key])}</b><del>{format(current.spec[key], key)}</del><ins>{format(draft[key], key)}</ins></div>)}
      </div>}
      <p>{t('当前人设版本：{revision}', { revision: current.revision })}{restore !== null && ` · ${t('将恢复版本 {revision} 的内容并创建新版本', { revision: restore })}`}</p>
      {!validPersona(draft) && <p role="alert">{t('名字不能为空；性格最多 5 项，每项最多 40 字。请检查字段长度。')}</p>}
      <Checkbox disabled={disabled || (!differences.length && restore === null)} checked={confirmed} onChange={event => setConfirmed(event.target.checked)}>{t('我已核对差异，确认保存人设')}</Checkbox>
      <Space wrap className={styles.actions}>
        <Button type="primary" disabled={disabled || !confirmed || !validPersona(draft) || (!differences.length && restore === null)} onClick={() => void submit()}>{t('保存人设')}</Button>
        <Button disabled={disabled} onClick={() => { setDraft(structuredClone(current.spec)); setRestore(null); setConfirmed(false); setError(''); }}>{t('取消编辑')}</Button>
        <Button disabled={disabled} onClick={() => history ? setHistory(null) : void loadHistory()}>{t(history ? '收起人设历史' : '查看人设历史')}</Button>
      </Space>
      {history && <ol className={styles.history}>{history.map(item => <li key={item.revision}><span>{t('人设版本 {revision}', { revision: item.revision })} · {item.spec.display_name}</span><Button disabled={disabled || item.revision === current.revision} onClick={() => { setDraft(structuredClone(item.spec)); setRestore(item.revision); setConfirmed(false); }}>{t('恢复此版本到草稿')}</Button></li>)}</ol>}
    </>}
  </section>;
}
function format(value: PersonaSpec[keyof PersonaSpec], key: keyof PersonaSpec): string {
  if (key === 'verbosity') return t(VERBOSITY[value as PersonaSpec['verbosity']]);
  if (key === 'enabled_activity_templates') return (value as PersonaSpec['enabled_activity_templates']).map(id => t(ACTIVITIES[id])).join(', ') || '—';
  return (Array.isArray(value) ? value.join(', ') : value) || '—';
}

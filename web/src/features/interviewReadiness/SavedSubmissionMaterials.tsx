import { useEffect, useId, useRef, useState } from 'react';
import { t, useLocale } from '@/i18n';
import { listSubmissionSnapshots } from '@/services/applicationOutcomes';
import type { ApplicationSubmissionSnapshot } from '@/types/applicationOutcome';
import styles from './InterviewReadinessCenter.module.css';

type Row = ApplicationSubmissionSnapshot;
type View = { kind: 'closed' | 'loading' | 'error' } | { kind: 'ready'; rows: Row[] };
type ArchiveObject = Record<string, unknown>;

function isObject(value: unknown): value is ArchiveObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function visibleRows(input: unknown, applicationId: number): Row[] {
  if (!Array.isArray(input)) throw new Error('Invalid submission archives');
  const ids = new Set<number>();
  const conflicts = new Set<number>();
  for (const row of input) {
    if (!isObject(row) || typeof row.id !== 'number' || !Number.isSafeInteger(row.id) || row.id <= 0) continue;
    if (ids.has(row.id)) conflicts.add(row.id);
    ids.add(row.id);
  }
  return input.filter((row): row is Row => isObject(row) && row.application_id === applicationId
    && typeof row.id === 'number' && ids.has(row.id) && !conflicts.has(row.id));
}

function hasContent(value: unknown): boolean {
  if (typeof value === 'string') return !!value.trim();
  if (Array.isArray(value)) return value.some(hasContent);
  if (isObject(value)) return Object.values(value).some(hasContent);
  return typeof value === 'boolean' || typeof value === 'number';
}

// Render archived JSON as text only. Don't resolve live resume fields/URLs or
// discard unfamiliar fields merely because today's editor doesn't know them.
function ArchivedValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <ul>{value.filter(hasContent).map((item, index) => <li key={index}><ArchivedValue value={item} /></li>)}</ul>;
  if (isObject(value)) return <dl>{Object.entries(value).filter(([, item]) => hasContent(item)).map(([field, item]) => (
    <div key={field}><dt>{field}</dt><dd><ArchivedValue value={item} /></dd></div>
  ))}</dl>;
  return <span className={styles.archiveText}>{String(value)}</span>;
}

function isStructuredSection(key: string, value: unknown): boolean {
  if (!hasContent(value)) return false;
  if (key === 'education' || key === 'experience' || key === 'projects') {
    return Array.isArray(value) && value.every(isObject);
  }
  if (key === 'career_intent' || key === 'contact') return isObject(value);
  if (key === 'additional_text') return typeof value === 'string';
  return typeof value === 'string' || Array.isArray(value) || isObject(value); // Skills has used all three representations.
}

function ArchiveContent({ row }: { row: Row }) {
  const resume = row.resume_snapshot;
  const structured = [
    ['career_intent', t("求职意向")], ['contact', t("联系方式")], ['education', t("教育经历")],
    ['experience', t("工作经历")], ['projects', t("项目经历")], ['skills', t("技能")], ['additional_text', t("简历补充内容")],
  ];
  const fields = isObject(resume) ? structured.filter(([key]) => isStructuredSection(key, resume[key])) : [];
  const extra = isObject(resume) ? Object.fromEntries(Object.entries(resume).filter(([key, value]) =>
    key !== 'raw_text' && !fields.some(([section]) => section === key) && hasContent(value))) : resume;
  const raw = isObject(resume) ? resume.raw_text : undefined;
  return <article data-testid="submission-archive-record">
    <h4>{t("已保存的结构化简历")}</h4>
    {isObject(resume) && fields.map(([key, label]) => (
      <section key={key}><h5>{label}</h5><ArchivedValue value={resume[key]} /></section>
    ))}
    {fields.length === 0 && <p>{t("此记录没有可展示的结构化简历内容。")}</p>}
    {hasContent(extra) && <details><summary>{t("其他历史简历字段（只读）")}</summary><ArchivedValue value={extra} /></details>}
    {hasContent(raw) && <details><summary>{t("简历原始提取文本（非结构化）")}</summary><ArchivedValue value={raw} /></details>}
    <h4>{t("已保存的岗位描述（完整归档）")}</h4>
    {typeof row.jd_snapshot === 'string' && row.jd_snapshot.trim()
      ? <pre className={styles.archiveText}>{row.jd_snapshot}</pre>
      : <p>{t("此记录未保存岗位描述。")}</p>}
  </article>;
}

export default function SavedSubmissionMaterials({ applicationId }: { applicationId: number }) {
  useLocale();
  const panel = useId();
  const [view, setView] = useState<View>({ kind: 'closed' });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);

  const open = () => {
    request.current?.abort();
    const turn = new AbortController();
    request.current = turn;
    setSelectedId(null);
    setView({ kind: 'loading' });
    void listSubmissionSnapshots(applicationId, turn.signal).then((input) => {
      if (!turn.signal.aborted && request.current === turn) setView({ kind: 'ready', rows: visibleRows(input, applicationId) });
    }).catch(() => {
      if (!turn.signal.aborted && request.current === turn) setView({ kind: 'error' });
    });
  };
  const close = () => {
    request.current?.abort();
    request.current = null;
    setSelectedId(null);
    setView({ kind: 'closed' });
  };
  const selected = view.kind === 'ready' ? view.rows.find((row) => row.id === selectedId) : undefined;

  return <section className={styles.savedSubmission} data-testid="saved-submission-materials">
    <button className={styles.archiveButton} type="button" aria-expanded={view.kind !== 'closed'} aria-controls={panel}
      onClick={view.kind === 'closed' ? open : close}>
      {view.kind === 'closed' ? t("查看已保存的投递材料") : t("收起已保存的投递材料")}
    </button>
    <div id={panel} hidden={view.kind === 'closed'}>
      {view.kind !== 'closed' && <>
        <p>{t("这是你在 OfferPilot 保存的历史材料，不是用人方回执，也不会更改本次准备使用的简历或岗位描述。")}</p>
        {view.kind === 'loading' && <p role="status">{t("正在读取已保存的投递材料…")}</p>}
        {view.kind === 'error' && <div role="alert">
          <p>{t("暂时无法读取已保存的投递材料。")}</p>
          <button className={styles.archiveButton} type="button" onClick={open}>{t("重试")}</button>
        </div>}
        {view.kind === 'ready' && (view.rows.length === 0 ? <p>{t("这份投递还没有已保存的材料记录。")}</p> : <>
          <label className={styles.archiveSelector}>{t("选择一条已保存的投递记录")}
            <select value={selectedId ?? ''} onChange={(event) => {
              const row = view.rows.find((item) => String(item.id) === event.target.value);
              setSelectedId(row?.id ?? null);
            }}>
              <option value="">{t("请选择一条记录")}</option>
              {view.rows.map((row) => <option value={row.id} key={row.id}>
                {[row.submitted_at, `#${row.id}`].filter((value) => typeof value === 'string' && value.trim()).join(' · ')}
              </option>)}
            </select>
          </label>
          {selected && <ArchiveContent key={selected.id} row={selected} />}
        </>)}
      </>}
    </div>
  </section>;
}

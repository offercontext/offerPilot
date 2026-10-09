import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal } from 'antd';
import { importJobMail, JOB_MAIL_QUERY_KEY } from '@/services/jobMail';
import type { JobMailImportInput } from '@/types/jobMail';
import styles from './jobMail.module.css';

export default function JobMailImport({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<JobMailImportInput>({ subject: '', sender: '', received_at: '', body_text: '' });
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const lock = useRef(false);
  const bodyBytes = new TextEncoder().encode(draft.body_text).byteLength;
  function change(field: keyof JobMailImportInput, value: string) {
    setDraft((old) => ({ ...old, [field]: value }));
    setPreview(false);
    setError('');
  }
  function prepare() {
    if (!draft.body_text.trim() || !draft.subject.trim()) { setError('请填写主题和单封邮件正文。'); return; }
    if (bodyBytes > 30_000) { setError('正文超过 30 KB，请只保留本次安排的相关原文，不要粘贴附件或整段往来。'); return; }
    if (!/T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/.test(draft.received_at) || !Number.isFinite(Date.parse(draft.received_at))) {
      setError('请按邮件核实接收时间，填写含时区的完整时间。'); return;
    }
    setError(''); setPreview(true);
  }
  async function submit() {
    if (lock.current || !preview) return;
    lock.current = true; setBusy(true); setError('');
    try {
      await importJobMail(draft);
      setDone(true);
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
    } catch {
      setError('导入未确认成功，请到“今日 → 提醒”检查已有建议后再操作。不会自动重试或写入正式日程。');
    } finally { lock.current = false; setBusy(false); }
  }
  return <Modal open title="粘贴单封邮件" onCancel={onClose} footer={null} width={760} maskClosable={!busy} closable={!busy} keyboard={!busy}>
    {done ? <div className={styles.section}>
      <Alert type="success" showIcon message="邮件文本已处理" description="请到“今日 → 提醒 → 邮件待确认”核对建议。重复文本会去重；没有写入投递或日程。" />
      <Button onClick={onClose}>完成</Button>
    </div> : <div className={styles.form}>
      <Alert type="info" showIcon message="仅使用本地安全规则生成建议，不调用模型" description="手动提供的发件人身份未经验证。邮件正文是不可信数据；不会执行其中的指令、加载 HTML、图片或外链。请勿粘贴邮箱授权码、密码或无关私人内容。" />
      {!preview ? <>
        <label className={styles.field}>邮件主题<input className={styles.input} value={draft.subject} maxLength={500} onChange={(e) => change('subject', e.target.value)} /></label>
        <label className={styles.field}>发件地址（手动提供，未经验证）<input className={styles.input} value={draft.sender} maxLength={320} onChange={(e) => change('sender', e.target.value)} /></label>
        <label className={styles.field}>实际接收时间（含时区）<input className={styles.input} placeholder="2026-10-09T15:00:00+08:00" value={draft.received_at} onChange={(e) => change('received_at', e.target.value)} /></label>
        <label className={styles.field}>邮件正文<textarea className={styles.textarea} rows={9} value={draft.body_text} onChange={(e) => change('body_text', e.target.value)} /></label>
        <p className={styles.muted}>{bodyBytes.toLocaleString()} / 30,000 字节。不读取剪贴板或邮箱网页。</p>
      </> : <>
        <h4>将提交给当前 OfferPilot 后端的文本</h4>
        <p className={styles.muted}>主题：{draft.subject}<br />发件人：{draft.sender || '未提供'}<br />接收时间：{draft.received_at}</p>
        <pre className={styles.evidence}>{draft.body_text}</pre>
        <p className={styles.muted}>此操作只生成待核对建议。完整时间和正时长的简单事件，仍需稍后逐项确认；截止、窗口、Offer、取消及复杂改期需人工处理。</p>
      </>}
      {error && <Alert type="error" showIcon message={error} />}
      <div className={styles.actions}>
        {preview ? <><Button type="primary" loading={busy} onClick={() => void submit()}>提交文本并生成待确认建议</Button><Button disabled={busy} onClick={() => setPreview(false)}>返回编辑</Button></> : <Button type="primary" onClick={prepare}>预览将提交的文本</Button>}
        <Button disabled={busy} onClick={onClose}>取消</Button>
      </div>
    </div>}
  </Modal>;
}

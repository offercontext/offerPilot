import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal } from 'antd';
import {
  cancelJobMailSecureSetup, getJobMailSecureCapability, JOB_MAIL_SECURE_CAPABILITY_KEY,
  saveJobMailSecureSetup, startJobMailSecureSetup, testJobMailSecureSetup,
} from '@/services/jobMailSecureSetup';
import { JOB_MAIL_QUERY_KEY } from '@/services/jobMail';
import type { JobMailFolderChoice, JobMailSetupSession } from '@/types/jobMailSecureSetup';
import { canEnterMailCredential, isLoopbackSetupPage, mailSecureCapabilityReason, mailSecureSetupErrorText } from './secureSetupModel';
import styles from './jobMail.module.css';

type Phase = 'intro' | 'credentials' | 'folders' | 'expired' | 'unknown' | 'complete';
export default function JobMailSecureSetup({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const capabilityQuery = useQuery({ queryKey: JOB_MAIL_SECURE_CAPABILITY_KEY, queryFn: getJobMailSecureCapability, retry: false, refetchInterval: 5_000 });
  const allowed = !capabilityQuery.isError && canEnterMailCredential(capabilityQuery.data, window.location);
  const [phase, setPhase] = useState<Phase>('intro');
  const [setupConsent, setSetupConsent] = useState(false);
  const [testConsent, setTestConsent] = useState(false);
  const [saveConsent, setSaveConsent] = useState(false);
  const [email, setEmail] = useState('');
  const [hasAuthorizationCode, setHasAuthorizationCode] = useState(false);
  const [maskedEmail, setMaskedEmail] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const [folders, setFolders] = useState<JobMailFolderChoice[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [backfill, setBackfill] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const passwordInput = useRef<HTMLInputElement | null>(null);
  const session = useRef<JobMailSetupSession | null>(null);
  const activeRequest = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const live = useRef(true);
  const generation = useRef(0);

  function clearAuthorizationCode() {
    if (passwordInput.current) passwordInput.current.value = '';
    if (live.current) setHasAuthorizationCode(false);
  }
  function releaseSession() {
    generation.current += 1;
    activeRequest.current?.abort(); activeRequest.current = null;
    clearAuthorizationCode();
    const current = session.current;
    session.current = null;
    if (current) void cancelJobMailSecureSetup(current.setup_token).catch(() => undefined);
  }
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; releaseSession(); };
    // Session and input are mutable refs so StrictMode/unmount cleanup sees the current values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!allowed && session.current) {
      releaseSession(); lock.current = false; setBusy(''); setPhase('intro'); setTestConsent(false); setSaveConsent(false);
      setError('安全能力校验已失效，已清空本页输入并请求释放临时会话。请重新检测。');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allowed]);
  useEffect(() => {
    if (!expiresAt || phase === 'complete') return;
    const checkExpiry = () => {
      if (session.current && Date.now() >= Date.parse(expiresAt)) {
        releaseSession(); lock.current = false; setBusy(''); setPhase('expired'); setSaveConsent(false); setTestConsent(false);
        setError('安全配置会话已过期。临时授权码已请求释放；请重新开始，不会自动重试。');
      }
    };
    checkExpiry(); const timer = window.setInterval(checkExpiry, 1_000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expiresAt, phase]);

  function currentSession(): JobMailSetupSession | null {
    if (!allowed || !session.current || !Number.isFinite(Date.parse(session.current.expires_at)) || Date.parse(session.current.expires_at) <= Date.now()) {
      releaseSession(); setPhase('expired'); setError('安全配置会话不可用或已过期，请重新开始。'); return null;
    }
    return session.current;
  }
  async function start() {
    if (lock.current || !allowed || !setupConsent) return;
    releaseSession(); const attempt = generation.current;
    lock.current = true; setBusy('start'); setError(''); setSaveConsent(false); setTestConsent(false); setSelected([]); setFolders([]); setBackfill(false);
    try {
      const result = await startJobMailSecureSetup();
      if (!live.current || generation.current !== attempt) {
        if (typeof result.setup_token === 'string') void cancelJobMailSecureSetup(result.setup_token).catch(() => undefined);
        return;
      }
      if (!result.setup_token || !Number.isFinite(Date.parse(result.expires_at)) || Date.parse(result.expires_at) <= Date.now()) {
        if (typeof result.setup_token === 'string') void cancelJobMailSecureSetup(result.setup_token).catch(() => undefined);
        throw new Error('invalid session');
      }
      session.current = { setup_token: result.setup_token, expires_at: result.expires_at };
      setExpiresAt(result.expires_at); setPhase('credentials');
    } catch (err) { if (live.current && generation.current === attempt) setError(mailSecureSetupErrorText(err)); }
    finally { if (generation.current === attempt) { lock.current = false; if (live.current) setBusy(''); } }
  }
  async function test() {
    if (lock.current || !testConsent || !hasAuthorizationCode || !/^[^\s@]+@qq\.com$/i.test(email)) return;
    const current = currentSession(); if (!current) return;
    const input = passwordInput.current;
    if (!input?.value.trim()) { clearAuthorizationCode(); return; }
    const attempt = generation.current;
    lock.current = true; setBusy('test'); setError('');
    const controller = new AbortController(); activeRequest.current = controller;
    // Only the explicit user click reads this uncontrolled password field.
    // Clear the DOM synchronously; never copy it into React state, storage or diagnostics.
    const request = testJobMailSecureSetup({ setup_token: current.setup_token, email: email.trim(), authorization_code: input.value, explicit_test_consent: true }, controller.signal);
    clearAuthorizationCode();
    try {
      const result = await request;
      if (!live.current || generation.current !== attempt) return;
      if (result.setup_token !== current.setup_token || !Number.isFinite(Date.parse(result.expires_at)) || Date.parse(result.expires_at) <= Date.now() || !Array.isArray(result.folders)) throw new Error('invalid session');
      session.current = { setup_token: result.setup_token, expires_at: result.expires_at };
      setExpiresAt(result.expires_at); setMaskedEmail(result.email_masked); setFolders(result.folders); setSelected([]); setSaveConsent(false); setPhase('folders');
    } catch (err) {
      if (live.current && generation.current === attempt) { releaseSession(); lock.current = false; setBusy(''); setPhase('unknown'); setError(mailSecureSetupErrorText(err)); }
    } finally { if (generation.current === attempt) { lock.current = false; if (live.current) setBusy(''); activeRequest.current = null; } }
  }
  async function save() {
    if (lock.current || !saveConsent || !selected.length) return;
    const current = currentSession(); if (!current) return;
    const attempt = generation.current;
    lock.current = true; setBusy('save'); setError('');
    const controller = new AbortController(); activeRequest.current = controller;
    try {
      const result = await saveJobMailSecureSetup({ setup_token: current.setup_token, folder_ids: selected, backfill_days: backfill ? 7 : 0, explicit_save_consent: true }, controller.signal);
      if (!live.current || generation.current !== attempt) return;
      if (result.connection?.provider !== 'qq' || result.connection.status !== 'connected' || result.connection.sync_mode !== 'manual') throw new Error('invalid receipt');
      session.current = null; setPhase('complete'); setExpiresAt('');
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
    } catch (err) {
      if (live.current && generation.current === attempt) { releaseSession(); lock.current = false; setBusy(''); setPhase('unknown'); setError(mailSecureSetupErrorText(err)); }
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
    } finally { if (generation.current === attempt) { lock.current = false; if (live.current) setBusy(''); activeRequest.current = null; } }
  }
  function close() { releaseSession(); onClose(); }
  function restart() { releaseSession(); setPhase('intro'); setSetupConsent(false); setTestConsent(false); setSaveConsent(false); setError(''); }
  const excludedSelected = folders.some((folder) => selected.includes(folder.id) && folder.excluded_by_default);
  const readyLabel = capabilityQuery.data?.configured ? '已配置' : allowed ? '原生凭据库可用' : '当前不可配置';
  return <Modal open title="安全配置 QQ 邮箱" onCancel={close} footer={null} width={720} maskClosable={false} keyboard={busy !== 'save'} closable={busy !== 'save'}>
    <div className={styles.form}>
      <Alert type={allowed ? 'info' : 'warning'} showIcon message={readyLabel} description={!isLoopbackSetupPage(window.location) ? '请在后端所在设备直接打开 localhost 或回环地址。远程部署的安全输入尚未验证，当前页面不接收授权码。' : capabilityQuery.isError ? '安全能力读取失败，暂不接收授权码。请重试检测，或继续手动粘贴邮件。' : mailSecureCapabilityReason(capabilityQuery.data)} />
      <p className={styles.muted}>凭据库：{capabilityQuery.data?.backend || '尚未确认'}。仅使用 QQ 官方 imap.qq.com:993 并校验 TLS 证书。不向模型发送授权码；不放入聊天、截图、日志、代码仓库或普通备份。</p>
      {!allowed && <Button loading={capabilityQuery.isFetching} onClick={() => void capabilityQuery.refetch()}>重新检测安全能力</Button>}
      {phase === 'complete' ? <>
        <Alert type="success" showIcon message="QQ 邮箱已安全配置，当前为手动模式" description="已保存明确选择的范围。没有读取正文或启动自动检查；请关闭此窗口后按需点击“立即同步”。" />
        <Button type="primary" onClick={close}>完成</Button>
      </> : allowed && phase === 'intro' ? <>
        <p>授权码可能具备收发邮件能力，目录选择只是产品读取约束。此功能只使用只读 IMAP 获取你选定的邮件，不发送、移动、删除或标为已读。</p>
        <label className={styles.check}><input type="checkbox" checked={setupConsent} disabled={!!busy} onChange={(event) => setSetupConsent(event.target.checked)} />我将在此本地页面亲自输入授权码，开始一个短期安全配置会话；暂不保存授权码</label>
        <div><Button type="primary" disabled={!setupConsent || !!busy} loading={busy === 'start'} onClick={() => void start()}>开始安全配置</Button></div>
      </> : allowed && phase === 'credentials' ? <>
        <label className={styles.field}>QQ 邮箱地址<input className={styles.input} type="email" autoComplete="off" aria-label="QQ 邮箱地址" value={email} disabled={!!busy} onChange={(event) => { setEmail(event.target.value); setTestConsent(false); }} placeholder="你的邮箱@qq.com" /></label>
        <label className={styles.field}>QQ 邮箱授权码（由你亲自输入）<input ref={passwordInput} className={styles.input} type="password" autoComplete="off" spellCheck={false} maxLength={128} aria-label="QQ 邮箱授权码" disabled={!!busy} onChange={(event) => { setHasAuthorizationCode(!!event.target.value.trim()); setTestConsent(false); }} /></label>
        <p className={styles.muted}>这是邮箱授权码，不是 QQ 登录密码。页面不回显、不保留输入；提交验证或关闭时立即清空。请勿截图或把授权码发给助手。</p>
        <label className={styles.check}><input type="checkbox" checked={testConsent} disabled={!!busy} onChange={(event) => setTestConsent(event.target.checked)} />我同意现在使用上述授权码连接 QQ 官方服务器，仅验证登录并获取目录列表；不读取正文，不保存到凭据库</label>
        <div className={styles.actions}><Button type="primary" disabled={!!busy || !testConsent || !hasAuthorizationCode || !/^[^\s@]+@qq\.com$/i.test(email)} loading={busy === 'test'} onClick={() => void test()}>确认仅验证登录与目录</Button><Button onClick={close}>取消并清空输入</Button></div>
      </> : allowed && phase === 'folders' ? <>
        <Alert type="success" message={`登录与目录验证成功：${maskedEmail}`} description="授权码仅在短期服务端会话中等待你的保存决定。尚未保存，也未读取邮件正文。" />
        <h4>明确选择读取目录</h4>
        <div className={styles.form}>{folders.map((folder) => <label className={styles.check} key={folder.id}><input type="checkbox" disabled={!!busy || !folder.selectable} checked={selected.includes(folder.id)} onChange={(event) => { setSelected((current) => event.target.checked ? [...current, folder.id] : current.filter((id) => id !== folder.id)); setSaveConsent(false); }} /><span>{folder.name}{!folder.selectable ? '（不可选择）' : folder.excluded_by_default ? '（默认排除，需你主动选择）' : ''}</span></label>)}</div>
        {!folders.length && <Alert type="warning" message="没有可选择的目录，不能保存连接" />}
        {excludedSelected && <Alert type="warning" message="你选择了默认排除的目录，可能包含草稿、垃圾邮件或已删除邮件，请再次核对范围。" />}
        <label className={styles.check}><input type="checkbox" checked={backfill} disabled={!!busy} onChange={(event) => { setBackfill(event.target.checked); setSaveConsent(false); }} />另行允许回溯最近 7 天（默认仅检查连接后的新邮件）</label>
        <p className={styles.muted}>将保存：{maskedEmail}；范围：{folders.filter((folder) => selected.includes(folder.id)).map((folder) => folder.name).join('、') || '尚未选择'}；{backfill ? '最近 7 天' : '仅连接后的新邮件'}；同步方式：手动；AI 识别：关闭。</p>
        <label className={styles.check}><input type="checkbox" checked={saveConsent} disabled={!!busy} onChange={(event) => setSaveConsent(event.target.checked)} />我明确同意把此授权码保存到本机原生凭据库，并保存以上邮箱读取范围。保存只建立目录基线，不读取正文；以后仅按手动点击或我另行开启的自动计划检查。</label>
        <div className={styles.actions}><Button type="primary" loading={busy === 'save'} disabled={!!busy || !saveConsent || !selected.length} onClick={() => void save()}>确认保存凭据与范围</Button><Button disabled={!!busy} onClick={restart}>重新开始</Button></div>
      </> : (phase === 'expired' || phase === 'unknown') && <>
        <p className={styles.muted}>本次不会自动重试。取消请求未送达时，服务端临时秘密会随短期会话到期释放。保存结果未知时，请先刷新连接状态。</p>
        <div className={styles.actions}><Button onClick={() => void queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY })}>刷新连接状态</Button><Button disabled={!allowed} onClick={restart}>重新开始</Button></div>
      </>}
      {!!expiresAt && phase !== 'complete' && <p className={styles.muted}>本次短期会话有效至 {expiresAt}，刷新页面不会恢复授权码或自动重试。</p>}
      {error && <Alert type="error" showIcon message={error} />}
      {phase !== 'complete' && <Button disabled={busy === 'save'} onClick={close}>关闭并释放临时会话</Button>}
    </div>
  </Modal>;
}

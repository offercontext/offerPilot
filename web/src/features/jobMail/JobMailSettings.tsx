import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Modal, Skeleton, Switch } from 'antd';
import {
  cancelJobMailSync, connectSyntheticJobMail, disconnectJobMail, getJobMailStatus,
  JOB_MAIL_QUERY_KEY, JOB_MAIL_STATUS_KEY, syncJobMail, updateJobMailSettings,
} from '@/services/jobMail';
import { mailErrorText } from './jobMailModel';
import JobMailImport from './JobMailImport';
import JobMailSecureSetup from './JobMailSecureSetup';
import { cancelJobMailSecureSetup, disconnectRealJobMail, getJobMailSecureCapability, JOB_MAIL_SECURE_CAPABILITY_KEY, startJobMailSecureSetup } from '@/services/jobMailSecureSetup';
import { canEnterMailCredential, isLoopbackSetupPage, mailSecureCapabilityReason, mailSecureSetupErrorText } from './secureSetupModel';
import styles from './jobMail.module.css';

export default function JobMailSettings() {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: JOB_MAIL_STATUS_KEY, queryFn: getJobMailStatus, retry: false, refetchInterval: 5_000 });
  const secureQuery = useQuery({ queryKey: JOB_MAIL_SECURE_CAPABILITY_KEY, queryFn: getJobMailSecureCapability, retry: false, refetchInterval: 15_000 });
  const [deleteConsent, setDeleteConsent] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [connectionInfo, setConnectionInfo] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [interval, setIntervalMinutes] = useState<number | ''>(15);
  const [folders, setFolders] = useState<string[]>([]);
  const [backfill, setBackfill] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [now, setNow] = useState(Date.now());
  const lock = useRef(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const connection = query.data?.connection;
  const connected = connection?.status === 'connected';
  const running = query.data?.run?.status === 'running';
  const realCredential = connection?.provider === 'qq' || secureQuery.data?.configured === true || secureQuery.data?.deletion_pending === true;
  const deletionPending = secureQuery.data?.deletion_pending === true;
  const realSetupAllowed = !secureQuery.isError && canEnterMailCredential(secureQuery.data, window.location);
  useEffect(() => {
    if (!dirty) { setAutomatic(connection?.sync_mode === 'automatic'); setIntervalMinutes(connection?.interval_minutes ?? 15); }
  }, [connection?.sync_mode, connection?.interval_minutes, dirty]);
  useEffect(() => {
    if (!connection?.not_before_at) return;
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, [connection?.not_before_at]);
  const waitSeconds = Math.max(0, Math.ceil((Date.parse(connection?.not_before_at ?? '') - now) / 1000)) || 0;
  const intervalValid = typeof interval === 'number' && Number.isInteger(interval) && interval >= 5 && interval <= 1440;
  async function runAction(name: string, action: () => Promise<unknown>, success: string) {
    if (lock.current || !query.data || query.isError) return;
    lock.current = true; setBusy(name); setError(''); setNotice('');
    try {
      await action();
      if (live.current) { setNotice(success); setDirty(false); setScopeOpen(false); setDisconnectOpen(false); }
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
    } catch (err) {
      if (live.current) setError(name === 'disconnect-real' ? mailSecureSetupErrorText(err) : mailErrorText(err, '操作未确认成功。请刷新状态后再操作；不会自动重试。'));
      await queryClient.invalidateQueries({ queryKey: JOB_MAIL_QUERY_KEY });
    } finally { lock.current = false; if (live.current) setBusy(''); }
  }
  function openScope() { setFolders(connected ? connection.folders : []); setBackfill(false); setScopeOpen(true); }
  function saveSchedule() {
    if (!intervalValid) { setError('同步间隔必须是 5–1440 之间的整数分钟。'); return; }
    void runAction('schedule', () => updateJobMailSettings({ sync_mode: automatic ? 'automatic' : 'manual', interval_minutes: Number(interval), ai_enabled: false }), automatic ? '自动同步已保存，下一次时间以服务器返回为准。' : '已关闭未来自动同步；仍可立即同步。正在运行的任务继续，可单独取消。');
  }
  function saveScope() {
    if (!folders.length) { setError('请明确选择至少一个读取文件夹。'); return; }
    if (connected) void runAction('scope', () => updateJobMailSettings({ sync_mode: connection.sync_mode, interval_minutes: connection.interval_minutes, folders }), '读取范围已保存。新目录不会自动纳入。');
    else void runAction('connect', () => connectSyntheticJobMail({ provider: 'synthetic', email: 'demo@qq.com', folders, backfill_days: backfill ? 7 : 0 }), '合成测试邮箱已连接，默认手动；尚未读取正文，请按需立即同步。');
  }
  function openDisconnect() { setDeleteConsent(false); setDisconnectOpen(true); }
  async function disconnectReal() {
    if (!deleteConsent || !isLoopbackSetupPage(window.location) || secureQuery.isError || !secureQuery.data) return;
    await runAction('disconnect-real', async () => {
      const session = await startJobMailSecureSetup('disconnect');
      try {
        if (!session.setup_token || !Number.isFinite(Date.parse(session.expires_at)) || Date.parse(session.expires_at) <= Date.now()) throw new Error('invalid session');
        const result = await disconnectRealJobMail(session.setup_token);
        if (result.connection?.status === 'connected') throw new Error('deletion unconfirmed');
        // A success response is not enough to claim deletion if status still reports pending.
        const capability = await getJobMailSecureCapability();
        if (capability.configured || capability.deletion_pending) throw new Error('deletion unconfirmed');
        return result;
      } finally {
        if (session.setup_token) await cancelJobMailSecureSetup(session.setup_token).catch(() => undefined);
      }
    }, '邮箱已断开且本机凭据删除已确认；已确认业务记录保留。QQ 侧撤销授权码仍需你操作。');
  }
  const unavailable = !!busy || query.isError || !query.data;
  const run = query.data?.run;
  const runLabels = { running: '运行中', completed: '本次已完成', failed: '本次失败', cancelled: '已取消本次', interrupted: '已中断' };
  return <section className={styles.panel} aria-labelledby="job-mail-settings-title">
    <div className={styles.heading}><div><h3 className={styles.title} id="job-mail-settings-title">求职邮箱</h3><p className={styles.muted}>默认手动检查，邮件建议必须逐项核对确认。</p></div><span className={styles.status}>{connected ? connection.provider === 'synthetic' ? '合成测试连接' : '已连接' : '未连接'}</span></div>
    <Alert type="info" showIcon message="求职邮件：安全连接与逐项确认" description="真实 QQ 邮箱连接须通过本机原生凭据库和本地安全访问检查。你亲自验证后，还需另行确认保存凭据与读取范围，默认手动。AI 识别当前不可用；仅使用安全规则提出建议，不调用模型。" />
    <div className={styles.stat}><span className={styles.muted}>原生凭据能力</span><span>{secureQuery.isError ? '读取失败，暂不接收授权码' : secureQuery.data?.configured ? '已配置' : secureQuery.data?.available ? '支持库可用' : '当前不可用或尚未确认'}</span><span className={styles.muted}>{mailSecureCapabilityReason(secureQuery.isError ? undefined : secureQuery.data)}</span></div>
    {deletionPending && <Alert type="error" showIcon message="本机凭据仍待删除" description="检查已停止，但凭据删除尚未成功，不能视为已清理。已确认业务事件保留。" action={<Button disabled={!!busy} onClick={openDisconnect}>重试删除凭据</Button>} />}
    {query.isPending ? <Skeleton active paragraph={{ rows: 3 }} /> : query.isError || !query.data ? <Alert type="error" showIcon message="邮箱状态读取失败" description="无法判断是否检查成功，这不代表没有新邮件。" action={<Button onClick={() => void query.refetch()}>重试</Button>} /> : <>
      <div className={styles.stats}>
        <div className={styles.stat}><span className={styles.muted}>邮件检查运行在</span><span>{query.data.execution_location}</span></div>
        <div className={styles.stat}><span className={styles.muted}>每日候选预算</span><span>已用 {query.data.budget.used} / {query.data.budget.limit}，剩余 {query.data.budget.remaining}</span></div>
        <div className={styles.stat}><span className={styles.muted}>模型处理</span><span>未启用；不会调用模型</span></div>
        <div className={styles.stat}><span className={styles.muted}>同步模式</span><span>{connected && connection.sync_mode === 'automatic' ? '自动同步已开启' : '手动（默认）'}</span></div>
      </div>
      {connected && <>
        <div className={styles.stats}>
          <div className={styles.stat}><span className={styles.muted}>邮箱</span><span>{connection.email_masked}</span></div>
          <div className={styles.stat}><span className={styles.muted}>明确选中的目录</span><span>{connection.folders.join('、') || '未选择'} · 范围版本 {connection.scope_version}</span></div>
          <div className={styles.stat}><span className={styles.muted}>开始检查范围</span><span>{connection.start_at}</span></div>
          <div className={styles.stat}><span className={styles.muted}>最近成功检查</span><span>{connection.last_success_at ?? '尚未成功检查'}</span></div>
          <div className={styles.stat}><span className={styles.muted}>最近尝试</span><span>{connection.last_attempt_at ?? '尚未尝试'}</span></div>
          <div className={styles.stat}><span className={styles.muted}>下次自动运行</span><span>{connection.next_run_at ?? '无定时计划'}</span></div>
        </div>
        <p className={styles.muted}>覆盖说明：只检查以上已选目录，自起始时间按已保存游标推进。最近成功时间不证明积压已全部覆盖；延期或失败的范围仍待处理。</p>
        <div className={styles.actions}>
          <Button type="primary" disabled={unavailable || running || waitSeconds > 0} loading={busy === 'sync'} onClick={() => void runAction('sync', syncJobMail, '已请求手动同步，请查看任务状态。')}>{running ? '正在同步' : waitSeconds > 0 ? `${waitSeconds} 秒后可同步` : '立即同步'}</Button>
          {running && run && <Button disabled={unavailable} loading={busy === 'cancel'} onClick={() => void runAction('cancel', () => cancelJobMailSync(run.id), '已请求取消本次；自动开关和未来计划保持原状。')}>取消本次</Button>}
          <Button disabled={unavailable || running} onClick={openScope}>调整读取范围</Button>
          <Button danger disabled={unavailable} onClick={openDisconnect}>断开邮箱</Button>
        </div>
        <div className={styles.form}>
          <div className={styles.actions}><Switch aria-label="自动同步" checked={automatic} disabled={unavailable} onChange={(checked) => { setAutomatic(checked); setDirty(true); setNotice(''); }} /><span>自动同步（可选，保存后生效）</span></div>
          <label className={styles.field}>检查间隔（5–1440 分钟）<input className={styles.input} style={{ maxWidth: 220 }} aria-label="同步间隔" type="number" min={5} max={1440} step={1} value={interval} disabled={unavailable} onChange={(e) => { setIntervalMinutes(e.target.value ? Number(e.target.value) : ''); setDirty(true); setNotice(''); }} /></label>
          <div><Button disabled={unavailable || !dirty || !intervalValid} loading={busy === 'schedule'} onClick={saveSchedule}>保存同步方式</Button>{dirty && <span className={styles.muted}> 有未保存的同步设置</span>}</div>
          <p className={styles.muted}>关闭自动仍可手动同步，正在运行的任务不会因此取消。自动检查依赖后端持续运行；休眠、关机或服务停止时不会检查。修改间隔从保存后重新计时，不补跑错过的轮次。</p>
        </div>
      </>}
      {run && <div className={styles.preview}>
        <strong>最近任务：{runLabels[run.status]}</strong>
        <p className={styles.muted}>扫描 {run.progress.scanned} · 候选 {run.progress.candidates} · 重复 {run.progress.duplicates} · 延期待处理 {run.progress.deferred}</p>
        {!!run.progress.failed_folders.length && <Alert type="warning" message={`以下目录未检查成功：${run.progress.failed_folders.join('、')}`} />}
        {run.error_code && <Alert type="warning" message={`检查未完整成功（${run.error_code}）`} description="请核对连接与范围，不要据此判断没有新安排。" />}
      </div>}
    </>}
    <div className={styles.actions}>
      <Button onClick={() => setImportOpen(true)}>粘贴单封邮件</Button>
      <Button onClick={() => setConnectionInfo(true)}>{realSetupAllowed ? '安全配置 QQ 邮箱' : '连接 QQ 邮箱说明'}</Button>
      {!connected && realCredential && !deletionPending && <Button danger disabled={unavailable} onClick={openDisconnect}>删除本机邮箱凭据</Button>}
      {!connected && query.data?.capabilities.synthetic_connection && <Button disabled={unavailable} onClick={openScope}>连接合成测试邮箱</Button>}
    </div>
    {notice && <Alert type="success" showIcon message={notice} />}{error && <Alert type="error" showIcon message={error} />}
    <details className={styles.muted}><summary>数据、安全与保留说明</summary><p>目录限制是产品读取范围，通常不是授权码的服务端权限隔离。授权码可能具备收发邮件能力，不能称为天然只读。授权码不得放进聊天、代码仓库、诊断或日志。</p><p>数据保存在后端所在主机。只处理你提交的文本或明确选定范围内的邮件，不发送给模型；未开放附件、图片 OCR 或外链读取。待确认片段最长保留 90 天，已处理片段 30 天后清理；清理后只保留结构化历史并标明原文不可用。</p></details>
    {connectionInfo && <JobMailSecureSetup onClose={() => setConnectionInfo(false)} />}
    <Modal open={scopeOpen} title={connected ? '核对新的读取范围' : '连接合成测试邮箱'} onCancel={() => setScopeOpen(false)} footer={null} closable={!busy} maskClosable={!busy} keyboard={!busy}>
      <div className={styles.form}>
        <Alert type="info" message={connected ? '仅保存明确选中的目录' : '仅限后端显式注入的合成测试环境，不访问真实 QQ 邮箱'} description="未选目录不读取，新目录不会自动纳入。保存连接不会读取正文。" />
        <div className={styles.form}>{(query.data?.available_folders ?? []).map((folder) => <label key={folder} className={styles.check}><input type="checkbox" checked={folders.includes(folder)} disabled={!!busy} onChange={(e) => setFolders((current) => e.target.checked ? [...current, folder] : current.filter((name) => name !== folder))} />{folder}</label>)}</div>
        {!connected && <label className={styles.check}><input type="checkbox" checked={backfill} disabled={!!busy} onChange={(e) => setBackfill(e.target.checked)} />另外回溯最近 7 天（默认只检查连接后的邮件）</label>}
        <p className={styles.muted}>确认范围：{folders.join('、') || '尚未选择'}。AI 识别关闭；所有业务变更仍须逐项确认。</p>
        <div className={styles.actions}><Button type="primary" disabled={unavailable || !folders.length} loading={busy === 'scope' || busy === 'connect'} onClick={saveScope}>确认范围并保存</Button><Button disabled={!!busy} onClick={() => setScopeOpen(false)}>取消</Button></div>
      </div>
    </Modal>
    <Modal open={disconnectOpen} title="断开求职邮箱？" onCancel={() => setDisconnectOpen(false)} footer={null} closable={!busy} maskClosable={!busy} keyboard={!busy}>
      <div className={styles.form}><p>断开将停止本次及未来检查，重连前不能手动或自动同步。已确认的业务记录保留。断开本地连接不等于在 QQ 撤销授权码；QQ 侧撤销需你自行操作。</p>{realCredential && <><label className={styles.check}><input type="checkbox" checked={deleteConsent} disabled={!!busy} onChange={(event) => setDeleteConsent(event.target.checked)} />我明确同意停止邮箱检查并删除本机系统凭据库中的此邮箱授权码；保留已确认的投递和业务事件</label><p className={styles.muted}>删除失败时会保留“待删除”状态，可重试；不会提前显示已删除。此操作仅支持后端所在设备的本地回环页面。</p></>}<div className={styles.actions}>{realCredential ? <Button danger loading={busy === 'disconnect-real'} disabled={unavailable || !deleteConsent || !isLoopbackSetupPage(window.location) || secureQuery.isError || !secureQuery.data} onClick={() => void disconnectReal()}>确认断开并删除凭据</Button> : <Button danger loading={busy === 'disconnect'} disabled={unavailable} onClick={() => void runAction('disconnect', disconnectJobMail, '已断开邮箱，停止检查；已确认记录保留。')}>确认断开</Button>}<Button disabled={!!busy} onClick={() => setDisconnectOpen(false)}>保留连接</Button></div></div>
    </Modal>
    {importOpen && <JobMailImport onClose={() => setImportOpen(false)} />}
  </section>;
}

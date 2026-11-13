import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Space, Typography } from 'antd';
import { isAxiosError } from 'axios';
import { t, useLocale } from '../i18n';
import { cancelRestore, confirmRestore, getRestoreStatus, previewRestore, type RestoreReceipt } from '../services/backupRestore';

function errorMessage(error: unknown): string {
  if (isAxiosError(error) && error.response) {
    if (error.response.status >= 500) return t('操作结果未确认。请刷新恢复状态，勿重复提交。');
    const code = error.response.data?.error;
    if (code === 'archive_schema_mismatch') return t('备份版本不兼容。原工作区未替换；请保留 ZIP，用匹配版本在独立目录读取。');
    if (code === 'archive_size_limit' || code === 'archive_manifest_limit') return t('备份超过恢复限额，未替换原工作区。');
    if (error.response.status === 401) return t('请重新登录后刷新恢复状态。');
    return t('预检或确认被拒绝。请检查备份完整性并刷新恢复状态。');
  }
  return t('操作结果未确认。请刷新恢复状态，勿重复提交。');
}

export function BackupRestore() {
  useLocale();
  const [receipt, setReceipt] = useState<RestoreReceipt>({ state: 'none' });
  const [busy, setBusy] = useState(false);
  const [unknown, setUnknown] = useState(true);
  const [error, setError] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [phrase, setPhrase] = useState('');
  const active = useRef(false);
  const upload = useRef<HTMLInputElement>(null);
  async function action(work: () => Promise<RestoreReceipt>) {
    if (active.current) return;
    active.current = true;
    setBusy(true); setError('');
    try {
      const next = await work();
      // A refresh may discover another tab's preview; consent never carries over.
      setAcknowledged(false); setPhrase('');
      setReceipt(next); setUnknown(false);
    } catch (failure) {
      setUnknown(true); setError(errorMessage(failure));
    } finally {
      active.current = false; setBusy(false);
    }
  }
  useEffect(() => { void action(getRestoreStatus); }, []);
  const pending = ['validated', 'queued', 'switching', 'installed'].includes(receipt.state);
  const labels: Record<RestoreReceipt['state'], string> = {
    none: t('没有待恢复任务'), validated: t('预检通过，等待确认'), queued: t('已确认，等待完整重启'),
    switching: t('恢复切换中'), installed: t('等待启动验证'), completed: t('恢复已完成'),
    cancelled: t('恢复已取消'), failed: t('恢复失败，原数据保留'), rolled_back: t('已回滚到原数据'),
  };
  const counts: Record<string, string> = {
    applications: t('投递记录'), resumes: t('简历记录'), conversations: t('会话记录'), knowledge_sources: t('知识来源记录'),
  };
  return <Space direction="vertical" style={{ width: '100%' }}>
    <Typography.Title level={5}>{t('从工作区 ZIP 恢复')}</Typography.Title>
    <Alert type="warning" showIcon message={t('恢复会替换当前工作区')} description={
      t('覆盖数据库、简历、会话图片和知识附件。保留本机模型及登录设置；关闭自动写入、主动任务和外部授权，取消待处理知识任务，邮箱需重新连接。当前版本备份不含 Journal 指纹密钥，跨工作区不保证续记旧运行；桌面和浏览器偏好不在恢复范围内。')
    } />
    <Typography.Text role="status">{labels[receipt.state]}</Typography.Text>
    {error && <Alert type="error" showIcon message={error} />}
    <Space wrap>
      <Button disabled={busy} onClick={() => void action(getRestoreStatus)}>{t('刷新恢复状态')}</Button>
      <Button disabled={busy || unknown || pending} onClick={() => upload.current?.click()}>{t('选择 ZIP 并预检')}</Button>
      <input ref={upload} type="file" accept=".zip,application/zip" aria-label={t('恢复备份文件')} hidden
        onChange={(event) => {
          const file = event.target.files?.[0]; event.target.value = '';
          if (!file || busy || unknown || pending) return;
          if (file.size > 256 * 1024 * 1024) { setError(t('备份文件不能超过 256 MiB')); return; }
          setAcknowledged(false); setPhrase('');
          void action(() => previewRestore(file));
        }} />
    </Space>
    {receipt.id && <Typography.Text copyable>{receipt.id}</Typography.Text>}
    {receipt.sha256 && <Typography.Paragraph style={{ overflowWrap: 'anywhere' }}>SHA-256: {receipt.sha256}</Typography.Paragraph>}
    {receipt.counts && <Typography.Paragraph>{Object.entries(receipt.counts).map(([key, count]) => `${counts[key] ?? key}: ${count}`).join(' · ')}</Typography.Paragraph>}
    {receipt.legacy && <Alert type="warning" message={t('旧包没有完整性清单：已检查结构和附件，但无法证明原始备份完整。')} />}
    {receipt.state === 'validated' && <>
      <Alert type="warning" message={t('确认后至重启前的新工作也会被覆盖。请先导出当前工作区。')} />
      <Checkbox checked={acknowledged} disabled={busy || unknown} onChange={(event) => setAcknowledged(event.target.checked)}>
        {t('我已保存当前工作，并理解覆盖范围及授权失效。')}
      </Checkbox>
      <Input aria-label={t('输入确认短语')} placeholder="REPLACE WORKSPACE" value={phrase}
        disabled={busy || unknown} onChange={(event) => setPhrase(event.target.value)} />
      <Button danger disabled={busy || unknown || !acknowledged || phrase !== 'REPLACE WORKSPACE'}
        onClick={() => void action(() => confirmRestore(receipt.id!, receipt.sha256!, phrase))}>{t('确认恢复并等待重启')}</Button>
    </>}
    {['validated', 'queued'].includes(receipt.state) && <Button disabled={busy || unknown}
      onClick={() => void action(() => cancelRestore(receipt.id!))}>{t('取消恢复')}</Button>}
    {receipt.state === 'queued' && <Alert type="info" message={t('请完整退出并重启服务')} description={
      t('桌面端完整退出后重新打开；网页端由服务运行者停止并重启后端，同时退出 CLI/MCP 等连接进程。刷新网页不会执行恢复。原目录保留，启动中断时下次启动自动回滚。')
    } />}
  </Space>;
}

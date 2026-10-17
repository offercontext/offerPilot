import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Divider, Space, Typography } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import type { UpdateState } from '@/features/desktopUpdates/types';

type UpdateAction = 'check' | 'download' | 'install';

const statusLabels: Record<UpdateState['status'], string> = {
  unavailable: '当前无法在线更新',
  idle: '可手动检查桌面更新',
  checking: '正在检查更新…',
  available: '发现新版本',
  downloading: '正在下载更新…',
  downloaded: '更新已下载，等待安装',
  installing: '正在准备退出并安装…',
  error: '更新操作失败',
};

export default function DesktopUpdatesCard() {
  const bridge = window.offerpilotUpdates;
  const [state, setState] = useState<UpdateState | null>(null);
  const [operationError, setOperationError] = useState('');
  const [action, setAction] = useState<UpdateAction | null>(null);
  const mounted = useRef(false);
  const inFlight = useRef(false);
  const stateRevision = useRef(0);

  useEffect(() => {
    if (!bridge) return;
    mounted.current = true;
    const revision = stateRevision.current;
    const unsubscribe = bridge.onState((next) => {
      stateRevision.current += 1;
      setOperationError('');
      setState(next);
    });
    void bridge.getState().then((next) => {
      if (mounted.current && stateRevision.current === revision) setState(next);
    }).catch(() => {
      if (mounted.current && stateRevision.current === revision) {
        setOperationError('无法读取桌面更新状态，请重试检查。');
      }
    });
    return () => {
      mounted.current = false;
      unsubscribe();
    };
  }, [bridge]);

  if (!bridge) return null;

  async function run(nextAction: UpdateAction) {
    if (!bridge || inFlight.current) return;
    inFlight.current = true;
    setAction(nextAction);
    setOperationError('');
    const revision = stateRevision.current;
    try {
      const next = await bridge[nextAction]();
      // Progress events are newer than a command's potentially stale reply.
      if (mounted.current && stateRevision.current === revision) setState(next);
    } catch (error) {
      if (mounted.current) {
        setOperationError(error instanceof Error ? error.message : '更新操作失败，请重试。');
      }
    } finally {
      inFlight.current = false;
      if (mounted.current) setAction(null);
    }
  }

  const busy = action !== null || state?.status === 'checking' || state?.status === 'downloading' || state?.status === 'installing';
  const error = operationError || (state?.status === 'error' ? state.reason || '更新操作失败，请重试。' : '');
  const percent = state?.percent !== undefined && Number.isFinite(state.percent)
    ? Math.min(100, Math.max(0, Math.round(state.percent)))
    : undefined;

  return (
    <section
      aria-labelledby="desktop-updates-title"
      style={{ display: 'grid', gap: 16, padding: 24, borderRadius: 16, border: '1px solid var(--op-border)', background: 'var(--op-surface)' }}
    >
      <div>
        <Typography.Title id="desktop-updates-title" level={4} style={{ margin: '0 0 4px' }}>桌面客户端更新</Typography.Title>
        <Typography.Text style={{ color: 'var(--op-muted)' }}>仅在你点击后检查和下载更新。</Typography.Text>
      </div>
      <Divider style={{ margin: 0 }} />
      <div>当前桌面版本：<strong>{state?.currentVersion || (operationError ? '未知' : '读取中…')}</strong></div>
      <div role="status" aria-live="polite">{state ? statusLabels[state.status] : '正在读取桌面更新状态…'}{state?.version ? ` · ${state.version}` : ''}</div>
      {state?.status === 'unavailable' ? <Alert type="info" showIcon message={state.reason || '此安装版本暂不支持在线更新。'} /> : null}
      {state?.reason && state.status !== 'unavailable' && state.status !== 'error' ? <Alert type="info" showIcon message={state.reason} /> : null}
      {state?.status === 'downloading' ? (
        <div role="progressbar" aria-label="桌面更新下载进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          {percent === undefined ? '下载进度等待中…' : `${percent}%`}
        </div>
      ) : null}
      {state?.releaseNotes ? (
        <div>
          <Typography.Text strong>更新说明</Typography.Text>
          <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', marginTop: 8 }}>{state.releaseNotes}</div>
        </div>
      ) : null}
      {error ? <Alert type="error" showIcon message={error} /> : null}
      {state?.status === 'downloaded' ? (
        <Typography.Text style={{ color: 'var(--op-muted)' }}>安装需要退出客户端。会检查已知草稿、运行中的任务和待确认操作；其他编辑器请先自行保存，退出前会再次确认。</Typography.Text>
      ) : null}
      <Space wrap>
        <Button
          icon={<ReloadOutlined />}
          disabled={busy || state?.status === 'unavailable' || (state?.status === 'downloaded' && !error) || (!state && !error)}
          loading={action === 'check' || state?.status === 'checking'}
          onClick={() => void run('check')}
        >{error ? '重新检查' : '检查更新'}</Button>
        {state?.status === 'available' && !error ? (
          <Button type="primary" icon={<DownloadOutlined />} disabled={busy} onClick={() => void run('download')}>下载更新</Button>
        ) : null}
        {state?.status === 'downloaded' && !error ? (
          <Button type="primary" disabled={busy} onClick={() => void run('install')}>退出并安装</Button>
        ) : null}
      </Space>
    </section>
  );
}

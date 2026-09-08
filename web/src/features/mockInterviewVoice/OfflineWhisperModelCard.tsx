import { useEffect, useId, useMemo, useState } from 'react';
import { Button, Popconfirm, Tag } from 'antd';
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { formatModelSize, OFFLINE_WHISPER_MANIFEST } from './offlineWhisperManifest';
import {
  offlineWhisperController,
  useOfflineWhisperState,
} from './offlineWhisperController';
import type { OfflineWhisperController } from './offlineWhisperTypes';
import styles from './OfflineWhisperModelCard.module.css';

type Props = {
  controller?: OfflineWhisperController;
  compact?: boolean;
  onActivityChange?: (activity: 'preparing_voice' | 'success' | 'error' | 'idle') => void;
};

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(0, Math.round(bytes / 1024))} KB`;
  return `${Math.round(bytes / 1024 / 1024)} MB`;
}

export default function OfflineWhisperModelCard({
  controller = offlineWhisperController,
  compact = false,
  onActivityChange,
}: Props) {
  const titleId = useId();
  const state = useOfflineWhisperState(controller);
  const [operationError, setOperationError] = useState('');

  useEffect(() => {
    if (state.status === 'checking') void controller.check();
  }, [controller, state.status]);

  const percent = useMemo(() => {
    if (state.status !== 'downloading' || !state.totalBytes || state.totalBytes <= 0) return undefined;
    return Math.min(100, Math.round(state.receivedBytes / state.totalBytes * 100));
  }, [state]);

  const prepare = async () => {
    setOperationError('');
    onActivityChange?.('preparing_voice');
    try {
      await controller.prepare();
      onActivityChange?.('success');
    } catch (error) {
      setOperationError(error instanceof Error ? error.message : '离线模型下载失败');
      onActivityChange?.('error');
    }
  };

  const remove = async () => {
    setOperationError('');
    try {
      await controller.remove();
      onActivityChange?.('idle');
    } catch {
      setOperationError('模型删除结果未知，请重新检查');
      onActivityChange?.('error');
      await controller.check();
    }
  };

  const busy = state.status === 'checking' || state.status === 'downloading' || state.status === 'loading';
  const backendLabel = state.status === 'ready' && state.backend === 'wasm' ? '兼容模式' : 'GPU 加速优先';
  const stateError = state.status === 'error' ? state.message : operationError;

  return (
    <section className={`${styles.card} ${compact ? styles.compact : ''}`} aria-labelledby={titleId}>
      <div className={styles.heading}>
        <span className={styles.icon} aria-hidden><SafetyCertificateOutlined /></span>
        <div className={styles.titleGroup}>
          <span className={styles.eyebrow}>离线语音转写</span>
          <h4 id={titleId}>离线语音转写</h4>
          <p>录音不会上传；模型只在你点击下载后保存到当前浏览器。</p>
        </div>
        {state.status === 'ready' ? <Tag color="green">已下载 · {backendLabel}</Tag> : null}
      </div>

      {state.status === 'downloading' ? (
        <div className={styles.progressBlock} aria-live="polite">
          <div className={styles.progressCopy}>
            <strong>正在准备离线语音能力</strong>
            <span>{formatBytes(state.receivedBytes)}{state.totalBytes ? ` / ${formatBytes(state.totalBytes)}` : ' · 总量确认中'}</span>
          </div>
          <div
            className={`${styles.progressTrack} ${percent === undefined ? styles.indeterminate : ''}`}
            role="progressbar"
            aria-label="离线模型下载进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
          >
            <span style={percent === undefined ? undefined : { width: `${percent}%` }} />
          </div>
        </div>
      ) : null}

      {state.status === 'loading' ? (
        <div className={styles.statusLine} role="status">正在校验并加载模型，完成后即可断网转写。</div>
      ) : null}

      {state.status === 'ready' ? (
        <div className={styles.readyGrid}>
          <div><span>模型</span><strong>{OFFLINE_WHISPER_MANIFEST.displayName}</strong></div>
          <div><span>缓存规模</span><strong>{state.cachedBytes ? formatBytes(state.cachedBytes) : formatModelSize()}</strong></div>
          <div><span>最长单次</span><strong>5 分钟</strong></div>
        </div>
      ) : null}

      {stateError ? <div className={styles.error} role="alert">{stateError}</div> : null}
      {state.status === 'incompatible' ? <div className={styles.error} role="alert">{state.reason}</div> : null}

      <div className={styles.footer}>
        <div className={styles.source}>
          <span>{formatModelSize()} · Apache-2.0</span>
          <a href={OFFLINE_WHISPER_MANIFEST.sourceUrl} target="_blank" rel="noreferrer">Hugging Face 来源</a>
        </div>
        <div className={styles.actions}>
          {state.status === 'ready' ? (
            <>
              <Button icon={<ReloadOutlined />} onClick={() => void controller.check()}>重新检查</Button>
              <Popconfirm
                title="删除离线模型？"
                description="删除后仍可录音和手工填写文字。"
                okText="确认删除"
                cancelText="保留模型"
                onConfirm={() => void remove()}
              >
                <Button danger icon={<DeleteOutlined />}>删除模型</Button>
              </Popconfirm>
            </>
          ) : (
            <Button
              type="primary"
              icon={<CloudDownloadOutlined />}
              loading={busy}
              disabled={busy}
              onClick={() => void prepare()}
            >
              {state.status === 'error' ? '重试下载' : '下载离线模型'}
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

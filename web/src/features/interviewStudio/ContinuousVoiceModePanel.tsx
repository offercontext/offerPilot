import { Button, Tag } from 'antd';
import type { ContinuousVoiceStatus } from '@/features/mockInterviewVoice/continuousVoiceSessionController';
import styles from './ContinuousVoiceModePanel.module.css';

type Props = {
  status: ContinuousVoiceStatus;
  countdownSeconds?: number | null;
  error?: string | null;
  disabled?: boolean;
  onEnable: () => void;
  onDisable?: () => void;
  onSkipReading?: () => void;
  onCancelCountdown?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onStop?: () => void;
  onFallback?: () => void;
};

const statusCopy: Record<ContinuousVoiceStatus, string> = {
  disabled: '标准模式：连续语音不会自动开启',
  preflight: '正在检查本地语音能力',
  reading_question: '正在朗读当前问题',
  waiting_for_speech: '等待你开口',
  listening: '正在倾听你的回答',
  end_candidate: '检测到可能结束',
  transcribing: '正在本地整理语音',
  reviewing_transcript: '请核对并确认回答文字',
  submitting_confirmed_answer: '正在提交已确认回答',
  generating_next_question: '正在准备下一题',
  paused: '连续语音已暂停',
  fallback_standard: '已回到标准模式',
  result_unknown: '结果待确认，输入已冻结',
  completed: '连续语音本轮已完成',
  closed: '连续语音已关闭',
};

function isActive(status: ContinuousVoiceStatus): boolean {
  return !['disabled', 'fallback_standard', 'completed', 'closed'].includes(status);
}

export default function ContinuousVoiceModePanel({
  status,
  countdownSeconds,
  error,
  disabled = false,
  onEnable,
  onDisable,
  onSkipReading,
  onCancelCountdown,
  onPause,
  onResume,
  onStop,
  onFallback,
}: Props) {
  return (
    <section className={styles.panel} aria-label="连续语音模式" data-testid="continuous-voice-panel">
      <div className={styles.heading}>
        <div>
          <span className={styles.eyebrow}>可选语音体验</span>
          <strong>连续语音模式</strong>
        </div>
        <Tag color={isActive(status) ? 'purple' : status === 'fallback_standard' ? 'orange' : 'default'}>
          {statusCopy[status]}
        </Tag>
      </div>
      <p className={styles.description}>
        题目朗读、录音和本地整理都在当前浏览器完成；只有你确认文字后，才会进入面试提交流程。
      </p>
      {error ? <p className={styles.error} role="alert">{error}</p> : null}
      <div className={styles.actions}>
        {status === 'disabled' ? <Button type="primary" disabled={disabled} onClick={onEnable}>开启连续语音模式</Button> : null}
        {status === 'preflight' ? <Button disabled>正在准备麦克风…</Button> : null}
        {status === 'reading_question' ? <Button disabled={disabled} onClick={onSkipReading}>跳过朗读，开始回答</Button> : null}
        {status === 'waiting_for_speech' || status === 'listening' ? (
          <>
            <Button disabled={disabled} onClick={onStop}>结束本轮录音</Button>
            <Button disabled={disabled} onClick={onPause}>暂停连续模式</Button>
          </>
        ) : null}
        {status === 'end_candidate' ? (
          <>
            <span className={styles.countdown}>{countdownSeconds ?? 3} 秒后停止录音</span>
            <Button disabled={disabled} onClick={onCancelCountdown}>继续补充</Button>
          </>
        ) : null}
        {status === 'paused' ? <Button type="primary" disabled={disabled} onClick={onResume}>继续连续语音</Button> : null}
        {status === 'fallback_standard' || status === 'result_unknown' ? <Button disabled={status !== 'result_unknown' && disabled} onClick={onDisable ?? onFallback}>回到标准模式</Button> : null}
        {isActive(status) && status !== 'preflight' ? <Button type="link" disabled={disabled} onClick={onDisable}>切换标准模式</Button> : null}
        {status === 'preflight' ? <Button type="link" disabled={disabled} onClick={onDisable}>切换标准模式</Button> : null}
      </div>
    </section>
  );
}

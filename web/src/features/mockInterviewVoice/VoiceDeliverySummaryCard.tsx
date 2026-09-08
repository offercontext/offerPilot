import type { RefObject } from 'react';
import { AudioOutlined, ClockCircleOutlined, PauseCircleOutlined, SoundOutlined } from '@ant-design/icons';
import type { VoiceDeliverySummary } from './voiceDeliverySummary';
import styles from './VoiceDeliverySummaryCard.module.css';

type Props = {
  summary: VoiceDeliverySummary;
  transcriptRef?: RefObject<HTMLTextAreaElement>;
  audioRef?: RefObject<HTMLAudioElement>;
};

function formatDuration(milliseconds: number): string {
  const seconds = Math.max(0, Math.round(milliseconds / 1_000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function codePointOffsetToCodeUnit(text: string, offset: number): number {
  return Array.from(text).slice(0, Math.max(0, offset)).join('').length;
}

export default function VoiceDeliverySummaryCard({ summary, transcriptRef, audioRef }: Props) {
  const longestPause = summary.pauseRanges
    .slice()
    .sort((left, right) => (right[1] - right[0]) - (left[1] - left[0]))[0];
  const locateFiller = (offset: number, text: string) => {
    const textarea = transcriptRef?.current;
    if (!textarea) return;
    const transcript = textarea.value;
    const start = codePointOffsetToCodeUnit(transcript, offset);
    textarea.focus();
    textarea.setSelectionRange(start, start + text.length);
  };
  const locatePause = () => {
    if (!audioRef?.current || !longestPause) return;
    audioRef.current.currentTime = longestPause[0] / 1_000;
    const playback = audioRef.current.play();
    void playback?.catch(() => undefined);
  };

  return (
    <section className={styles.card} aria-label="表达节奏复盘">
      <header className={styles.header}>
        <div>
          <span className={styles.eyebrow}>DELIVERY REVIEW</span>
          <h4>表达节奏复盘</h4>
          <p>仅陈述本次录音的可测量事实，不评价表达能力或面试表现。</p>
        </div>
        <span className={styles.localBadge}><span aria-hidden>●</span> 本地分析</span>
      </header>
      <div className={styles.metrics}>
        <div className={styles.metric}><ClockCircleOutlined /><span>回答时长</span><strong>{formatDuration(summary.totalDurationMs)}</strong></div>
        <div className={styles.metric}><AudioOutlined /><span>有效发声</span><strong>{formatDuration(summary.voicedDurationMs)}</strong></div>
        <div className={styles.metric}><PauseCircleOutlined /><span>停顿次数</span><strong>{summary.pauseCount} 次</strong></div>
        <button className={styles.metricButton} type="button" onClick={locatePause} disabled={!audioRef?.current || !longestPause}>
          <PauseCircleOutlined /><span>最长停顿</span><strong>{(summary.longestPauseMs / 1_000).toFixed(1)} 秒</strong>
        </button>
        <div className={styles.metric}><SoundOutlined /><span>文字节奏</span><strong>{summary.speechRateCpm === undefined ? '样本不足' : `${summary.speechRateCpm} 字/分钟`}</strong></div>
      </div>
      <div className={styles.fillers}>
        <div><strong>口头语位置</strong><span>按确认后的文字统计，点击可定位。</span></div>
        <div className={styles.fillerList}>
          {summary.fillerOccurrences.length ? summary.fillerOccurrences.flatMap((item) => item.transcriptOffsets.map((offset, index) => (
            <button key={`${item.text}-${offset}-${index}`} type="button" onClick={() => locateFiller(offset, item.text)} disabled={!transcriptRef?.current}>
              {item.text} · 第 {offset + 1} 字
            </button>
          ))) : <span className={styles.empty}>本次未识别到默认口头语</span>}
        </div>
      </div>
      <details className={styles.method}>
        <summary>查看计算口径</summary>
        <p>停顿只统计首次与末次发声之间不少于 0.8 秒的静音；文字节奏使用确认文字的有效 Unicode 字符数除以总回答分钟数。</p>
      </details>
    </section>
  );
}

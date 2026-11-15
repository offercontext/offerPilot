import { t, useLocale } from '@/i18n';
import { CheckCircleOutlined, ClockCircleOutlined, SoundOutlined } from '@ant-design/icons';
import { Alert, Button, Input } from 'antd';
import type { VoiceCoachingFocusKind, VoiceCoachingPendingReview } from '@/types/voiceCoaching';
import styles from './VoiceCoachingSnapshotSaveCard.module.css';

interface Props {
  review: VoiceCoachingPendingReview;
  onChange: (patch: Partial<VoiceCoachingPendingReview>) => void;
  onSave: () => void;
  onSkip: () => void;
}

const focusOptions: Array<{ value: VoiceCoachingFocusKind | null; label: string }> = [
  { value: null, get label() { return t("暂不设置重点"); } },
  { value: 'long_pause_control', get label() { return t("减少长停顿"); } },
  { value: 'filler_reduction', get label() { return t("减少口头禅"); } },
  { value: 'pace_consistency', get label() { return t("稳定表达节奏"); } },
];

function duration(value: number): string {
  const seconds = Math.max(0, Math.round(value / 1_000));
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

export default function VoiceCoachingSnapshotSaveCard({ review, onChange, onSave, onSkip }: Props) {
  useLocale();
  const frozen = review.saveState === 'saving' || review.saveState === 'unknown' || review.saveState === 'conflict';
  const fillerCount = review.summary.fillerOccurrences.reduce((total, item) => total + item.count, 0);

  if (review.saveState === 'saved') {
    return (
      <section className={`${styles.card} ${styles.saved}`} aria-label={t("已保存的表达复盘")}>
        <CheckCircleOutlined className={styles.savedIcon} aria-hidden />
        <div>
          <strong>{t("表达复盘已保存")}</strong>
          <p>{t("录音未上传。历史中只保留本机测量结果、确认文字和你的反思。")}</p>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.card} aria-labelledby="voice-coaching-save-title">
      <header className={styles.header}>
        <div className={styles.iconWell}><SoundOutlined aria-hidden /></div>
        <div>
          <span className={styles.kicker}>LOCAL REVIEW</span>
          <h4 id="voice-coaching-save-title">{t("保存本次表达复盘")}</h4>
          <p>{t("这是独立的二次确认，不会自动保存。")}</p>
        </div>
      </header>

      {review.saveState === 'unknown' ? (
        <Alert
          type="warning"
          showIcon
          message={t("保存结果待确认")}
          description={t("输入已冻结。请使用原保存请求重试，避免生成重复记录。")}
        />
      ) : null}
      {review.saveState === 'conflict' ? (
        <Alert
          type="warning"
          showIcon
          message={t("已有另一份表达复盘")}
          description={t("这道回答已经保存了不同内容。当前草稿不会覆盖历史，你可以暂不保存后继续。")}
        />
      ) : null}

      <div className={styles.metrics} aria-label={t("本机表达测量摘要")}>
        <div className={styles.metricPrimary}>
          <ClockCircleOutlined aria-hidden />
          <span>{t("回答时长")}</span>
          <strong>{duration(review.summary.totalDurationMs)}</strong>
        </div>
        <div className={styles.metric}>
          <span>{t("最长停顿")}</span>
          <strong>{(review.summary.longestPauseMs / 1_000).toFixed(1)} {t("秒")}</strong>
        </div>
        <div className={styles.metric}>
          <span>{t("表达节奏")}</span>
          <strong>{review.summary.speechRateCpm ? t("{rate} 字/分", { rate: review.summary.speechRateCpm }) : t("暂不可测")}</strong>
        </div>
        <div className={styles.metric}>
          <span>{t("口头禅")}</span>
          <strong>{t("{count} 次", { count: fillerCount })}</strong>
        </div>
      </div>

      <div className={styles.field}>
        <label htmlFor="voice-coaching-reflection">{t("这次回答后，你想保留什么提醒")}</label>
        <Input.TextArea
          id="voice-coaching-reflection"
          value={review.reflectionText}
          disabled={frozen}
          maxLength={1_000}
          autoSize={{ minRows: 2, maxRows: 5 }}
          placeholder={t("例如：下一次先给结论，再补充排查过程。")}
          onChange={(event) => onChange({ reflectionText: event.target.value })}
        />
      </div>

      <fieldset className={styles.focusField} disabled={frozen}>
        <legend>{t("下次练习重点")}</legend>
        <div className={styles.focusOptions}>
          {focusOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={review.focusKind === option.value}
              onClick={() => onChange({ focusKind: option.value })}
            >
              {option.label}
            </button>
          ))}
        </div>
      </fieldset>

      <p className={styles.privacy}>{t("仅保存本机测量结果和你确认的文字。原始录音不会上传或写入历史。")}</p>
      <div className={styles.actions}>
        {review.saveState === 'unknown' ? (
          <Button type="primary" onClick={onSave}>{t("使用原保存请求重试")}</Button>
        ) : review.saveState === 'conflict' ? (
          <Button onClick={onSkip}>{t("暂不保存")}</Button>
        ) : (
          <>
            <Button onClick={onSkip} disabled={review.saveState === 'saving'}>{t("暂不保存")}</Button>
            <Button type="primary" onClick={onSave} loading={review.saveState === 'saving'}>
              {t("确认保存")}</Button>
          </>
        )}
      </div>
    </section>
  );
}

import { t, useLocale, getLocale } from '@/i18n';
import { useCallback, useEffect, useState } from 'react';
import {
  ArrowLeftOutlined,
  ArrowRightOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  LineChartOutlined,
  SoundOutlined,
} from '@ant-design/icons';
import { Alert, Button, Empty, Spin } from 'antd';
import {
  deleteVoiceCoachingSnapshot,
  getVoiceCoachingTrends,
  listVoiceCoachingSnapshots,
} from '@/services/voiceCoaching';
import type {
  VoiceCoachingRecommendation,
  VoiceCoachingSnapshot,
  VoiceCoachingTrends,
} from '@/types/voiceCoaching';
import styles from './VoiceCoachingGrowthView.module.css';

interface Props {
  onBack: () => void;
  onPractice: (recommendation: VoiceCoachingRecommendation) => void;
}

const focusLabels: Record<string, string> = {
  get long_pause_control() { return t("减少长停顿"); },
  get filler_reduction() { return t("减少口头禅"); },
  get pace_consistency() { return t("稳定表达节奏"); },
};

function duration(ms: number | null): string {
  if (ms === null) return t("数据不足");
  return t("{seconds} 秒", { seconds: (ms / 1_000).toFixed(1) });
}

function signed(value: number | null, suffix = ''): string {
  if (value === null) return t("暂无对比");
  return `${value > 0 ? '+' : ''}${Number.isInteger(value) ? value : value.toFixed(2)}${suffix}`;
}

function dateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(getLocale(), { hour12: false });
}

function fillerCount(snapshot: VoiceCoachingSnapshot): number {
  return snapshot.filler_occurrences.reduce((total, item) => total + item.count, 0);
}

export default function VoiceCoachingGrowthView({ onBack, onPractice }: Props) {
  useLocale();
  const [snapshots, setSnapshots] = useState<VoiceCoachingSnapshot[]>([]);
  const [trends, setTrends] = useState<VoiceCoachingTrends | null>(null);
  const [historyStatus, setHistoryStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [trendStatus, setTrendStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [deleteError, setDeleteError] = useState(false);
  const [deleteCandidateId, setDeleteCandidateId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setHistoryStatus('loading');
    setTrendStatus('loading');
    setDeleteError(false);
    const [historyResult, trendResult] = await Promise.allSettled([
      listVoiceCoachingSnapshots({ limit: 30 }),
      getVoiceCoachingTrends(),
    ]);
    if (historyResult.status === 'fulfilled') {
      setSnapshots(historyResult.value);
      setHistoryStatus('ready');
    } else {
      setSnapshots([]);
      setHistoryStatus('error');
    }
    if (trendResult.status === 'fulfilled') {
      setTrends(trendResult.value);
      setTrendStatus('ready');
    } else {
      setTrends(null);
      setTrendStatus('error');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const remove = async (snapshotId: number) => {
    setDeletingId(snapshotId);
    try {
      await deleteVoiceCoachingSnapshot(snapshotId);
      setDeleteCandidateId(null);
      setSnapshots((current) => current.filter((item) => item.id !== snapshotId));
      setTrends(null);
      setTrendStatus('loading');
      await load();
    } catch {
      setDeleteError(true);
    } finally {
      setDeletingId(null);
    }
  };

  const loading = historyStatus === 'loading' || trendStatus === 'loading';
  const error = historyStatus === 'error' || trendStatus === 'error' || deleteError;
  const recommendation = historyStatus === 'ready' && trendStatus === 'ready'
    ? trends?.recommendation ?? null
    : null;
  const actionableRecommendation = recommendation
    && snapshots.some((item) => item.id === recommendation.source_snapshot_id)
    ? recommendation
    : null;
  const longestPause = trends?.metrics.longest_pause_ms;
  const pace = trends?.metrics.speech_rate_cpm;
  const filler = trends?.metrics.filler_per_minute;

  return (
    <main className={`${styles.page} op-view-enter`} aria-labelledby="voice-growth-title">
      <header className={styles.pageHeader}>
        <div>
          <Button className={styles.back} type="text" icon={<ArrowLeftOutlined />} onClick={onBack}>
            {t("返回面试")}</Button>
          <span className={styles.eyebrow}>VOICE COACHING</span>
          <h1 id="voice-growth-title">{t("表达成长")}</h1>
          <p>{t("只汇总你主动保存的本机测量结果，不上传录音，不生成能力分。")}</p>
        </div>
        <div className={styles.localBadge}><SoundOutlined aria-hidden /> {t("本机测量 · 人工确认")}</div>
      </header>

      {error ? (
        <Alert
          type="warning"
          showIcon
          message={t("部分表达记录暂时无法加载")}
          action={<Button onClick={() => void load()}>{t("重新加载")}</Button>}
        />
      ) : null}
      {loading ? <div className={styles.loading}><Spin size="large" /><span>{t("正在整理表达记录")}</span></div> : null}

      {!loading && actionableRecommendation ? (
        <section className={styles.recommendation} aria-labelledby="voice-recommendation-title">
          <div className={styles.recommendationContent}>
            <span className={styles.eyebrow}>{t("下一次刻意练习")}</span>
            <h2 id="voice-recommendation-title">{actionableRecommendation.title}</h2>
            <p>{actionableRecommendation.reason}{t("。建议沿用一条真实问题，观察下一次本机测量结果。")}</p>
            <div className={styles.recommendationMeta}>
              <span>{t("基于 {count} 条已确认记录", { count: actionableRecommendation.source_snapshot_ids.length })}</span>
              <span>{t("不会改写历史")}</span>
              <span>{t("不会自动写入知识库")}</span>
            </div>
          </div>
          <Button
            type="primary"
            size="large"
            disabled={!actionableRecommendation.source_available}
            onClick={() => onPractice(actionableRecommendation)}
          >
            {t("针对这项再练一次")}{" "}<ArrowRightOutlined />
          </Button>
        </section>
      ) : null}

      {!loading && historyStatus === 'ready' && !actionableRecommendation && snapshots.length > 0 ? (
        <section className={styles.neutralNotice}>
          <LineChartOutlined aria-hidden />
          <div><strong>{t("继续积累真实练习")}</strong><span>{t("当前记录尚未形成稳定、可复现的训练方向。")}</span></div>
        </section>
      ) : null}

      {!loading && historyStatus === 'ready' && trendStatus === 'ready' && trends && snapshots.length > 0 ? (
        <section aria-labelledby="voice-trends-title">
          <div className={styles.sectionHeading}>
            <div><span className={styles.eyebrow}>RECENT WINDOWS</span><h2 id="voice-trends-title">{t("最近表达变化")}</h2></div>
            <span>{t("最近 {count} 次 vs 更早记录", { count: Math.min(trends.window_size, trends.snapshot_count) })}</span>
          </div>
          <div className={styles.metricGrid}>
            <article className={styles.metricCard}>
              <span>{t("最长停顿中位数")}</span>
              <strong>{duration(longestPause?.current_median ?? null)}</strong>
              <small>{t("较上一窗口")}{" "}{signed(longestPause?.delta ?? null, ' ms')}</small>
            </article>
            <article className={styles.metricCard}>
              <span>{t("语速中位数")}</span>
              <strong>{pace?.current_median === null || pace?.current_median === undefined ? t("数据不足") : t("{rate} 字/分", { rate: pace.current_median })}</strong>
              <small>{t("较上一窗口")}{" "}{signed(pace?.delta ?? null, t(" 字/分"))}</small>
            </article>
            <article className={styles.metricCard}>
              <span>{t("每分钟口头禅")}</span>
              <strong>{filler?.current_median === null || filler?.current_median === undefined ? t("数据不足") : t("{count} 次", { count: filler.current_median })}</strong>
              <small>{t("较上一窗口")}{" "}{signed(filler?.delta ?? null, t(" 次"))}</small>
            </article>
          </div>
          <p className={styles.metricFootnote}>{t("数值仅描述已保存样本，不代表面试能力、通过率或岗位匹配度。")}</p>
        </section>
      ) : null}

      {!loading && historyStatus === 'ready' ? (
        <section aria-labelledby="voice-history-title">
          <div className={styles.sectionHeading}>
            <div><span className={styles.eyebrow}>CONFIRMED HISTORY</span><h2 id="voice-history-title">{t("已确认的表达记录")}</h2></div>
            <span>{t("{count} 条", { count: snapshots.length })}</span>
          </div>
          {snapshots.length === 0 ? (
            <div className={styles.empty}><Empty description={t("完成一次语音回答并确认保存后，这里会出现表达记录")} /></div>
          ) : (
            <div className={styles.historyList}>
              {snapshots.map((snapshot) => (
                <article key={snapshot.id} className={styles.historyCard}>
                  <div className={styles.historyHeader}>
                    <div>
                      <span className={styles.historyContext}>{snapshot.company_name || t("历史投递")} · {snapshot.position_name || t("岗位信息不可用")}</span>
                      <h3>{snapshot.question_text}</h3>
                    </div>
                    <time dateTime={snapshot.created_at}>{dateTime(snapshot.created_at)}</time>
                  </div>
                  {!snapshot.source_available ? <div className={styles.sourceChanged}>{t("原投递来源已不可见，以下冻结记录仍可审阅。")}</div> : null}
                  <div className={styles.historyMetrics} aria-label={t("本次表达数据")}>
                    <span><ClockCircleOutlined aria-hidden /> {(snapshot.total_duration_ms / 1_000).toFixed(0)} {t("秒")}</span>
                    <span>{t("最长停顿 {seconds} 秒", { seconds: (snapshot.longest_pause_ms / 1_000).toFixed(1) })}</span>
                    <span>{snapshot.speech_rate_cpm ? t("{rate} 字/分", { rate: snapshot.speech_rate_cpm }) : t("语速暂不可测")}</span>
                    <span>{t("口头禅 {count} 次", { count: fillerCount(snapshot) })}</span>
                  </div>
                  {snapshot.reflection_text ? <blockquote>{snapshot.reflection_text}</blockquote> : null}
                  <details>
                    <summary>{t("查看已确认回答")}</summary>
                    <p>{snapshot.confirmed_answer_text}</p>
                  </details>
                  <div className={styles.historyFooter}>
                    <span>{snapshot.focus_kind ? t("练习重点：{focus}", { focus: focusLabels[snapshot.focus_kind] ?? snapshot.focus_kind }) : t("未设置下次重点")}</span>
                    {deleteCandidateId === snapshot.id ? (
                      <div className={styles.deleteConfirm} role="group" aria-label={t("确认删除表达记录")}>
                        <span>{t("删除后不可恢复")}</span>
                        <Button onClick={() => setDeleteCandidateId(null)}>{t("取消")}</Button>
                        <Button danger loading={deletingId === snapshot.id} onClick={() => void remove(snapshot.id)}>{t("确认删除")}</Button>
                      </div>
                    ) : (
                      <Button type="text" danger icon={<DeleteOutlined />} onClick={() => setDeleteCandidateId(snapshot.id)}>{t("删除")}</Button>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      ) : null}
    </main>
  );
}

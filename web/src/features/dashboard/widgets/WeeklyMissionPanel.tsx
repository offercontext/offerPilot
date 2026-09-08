import {
  CalendarOutlined,
  CheckCircleOutlined,
  FileDoneOutlined,
  FlagOutlined,
  ReadOutlined,
  TrophyOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';
import type { MissionMetric, MissionMetricKind, MissionMetricState } from '@/lib/missionControl';
import type { ViewMode } from '@/layout/navigation';
import styles from '../dashboard.module.css';

interface Props {
  metrics: MissionMetric[];
  unavailableKinds?: readonly MissionMetricKind[];
  onNavigate: (view: ViewMode) => void;
}

const ICONS: Record<MissionMetricKind, ReactNode> = {
  applications: <FlagOutlined />,
  followups: <CheckCircleOutlined />,
  interviews: <CalendarOutlined />,
  practice: <ReadOutlined />,
  materials: <FileDoneOutlined />,
  offers: <TrophyOutlined />,
};

const STATE_LABELS: Record<MissionMetricState, string> = {
  on_track: '正常',
  watch: '关注',
  behind: '落后',
  blocked: '阻塞',
};

export function deriveWeeklySummary(
  metrics: MissionMetric[],
  unavailableKinds: readonly MissionMetricKind[] = [],
): string {
  if (metrics.length === 0) return '暂无可汇总的关键事项';
  const unavailable = new Set(unavailableKinds);
  const knownMetrics = metrics.filter((metric) => !unavailable.has(metric.kind));
  const blocked = knownMetrics.filter((metric) => metric.state === 'blocked').length;
  const needsAttention = knownMetrics.filter((metric) => metric.state === 'watch' || metric.state === 'behind').length;
  const knownSummary = blocked > 0 && needsAttention > 0
    ? `${blocked} 项阻塞，${needsAttention} 项需要关注`
    : blocked > 0
      ? `${blocked} 项阻塞`
      : needsAttention > 0
        ? `${needsAttention} 项需要关注`
        : '';
  if (unavailable.size > 0) return knownSummary ? `${knownSummary}，部分数据暂不可用` : '部分数据暂不可用';
  if (knownSummary) return knownSummary;
  return '当前节奏正常';
}

function formatValue(metric: MissionMetric): string {
  if (metric.target != null && metric.target > 0) return `${metric.current} / ${metric.target}`;
  if (metric.kind === 'interviews') return `${metric.current} 场`;
  if (metric.kind === 'practice') return metric.current > 0 ? `${metric.current} 道待复习` : '已清空';
  if (metric.kind === 'followups') return metric.current > 0 ? `${metric.current} 个待跟进` : '节奏正常';
  if (metric.kind === 'offers') return metric.current > 0 ? `${metric.current} 个临近截止` : '暂无临近截止';
  return `${metric.current}`;
}

export default function WeeklyMissionPanel({ metrics, unavailableKinds = [], onNavigate }: Props) {
  const unavailable = new Set(unavailableKinds);
  const summary = deriveWeeklySummary(metrics, unavailableKinds);

  return (
    <section className={styles.weeklyMissionPanel} aria-labelledby="weekly-progress-title">
      <header className={styles.weeklyProgressHeader}>
        <div>
          <span className={styles.weeklyProgressEyebrow}>本周求职进度</span>
          <h2 id="weekly-progress-title" className={styles.weeklyProgressTitle}>
            关键事项：<strong>{summary}</strong>
          </h2>
        </div>
        <span className={styles.weeklyProgressHint}>根据当前任务状态汇总</span>
      </header>

      <div className={styles.weeklyProgressTrack} aria-hidden="true">
        <span className={styles.weeklyProgressFill} />
      </div>

      <div className={styles.weeklyMetricGrid}>
        {metrics.map((metric) => {
          const isUnavailable = unavailable.has(metric.kind);
          const displayValue = isUnavailable ? '数据暂不可用' : formatValue(metric);
          const displayState = isUnavailable ? '未知' : STATE_LABELS[metric.state];
          const displayReason = isUnavailable ? '数据加载尚未完成或暂时失败。' : metric.reason;
          return (
            <button
            key={metric.kind}
            type="button"
            className={`${styles.missionMetric} ${isUnavailable ? styles['metric-unavailable'] : styles[`metric-${metric.state}`]}`}
            onClick={() => onNavigate(metric.targetView)}
            aria-label={`${metric.label}，${displayValue}，${displayState}。${displayReason}`}
          >
            <span className={styles.metricIcon} aria-hidden="true">
              {ICONS[metric.kind]}
            </span>
            <span className={styles.metricBody}>
              <span className={styles.metricLabel}>{metric.label}</span>
              <span className={styles.metricReason}>{displayReason}</span>
            </span>
            <span className={styles.metricSummary}>
              <span className={`${styles.metricValue} op-tnum`}>{displayValue}</span>
              <span className={styles.metricState}>{displayState}</span>
            </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

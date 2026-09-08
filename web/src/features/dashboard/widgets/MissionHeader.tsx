import { Button, Tag } from 'antd';
import { ArrowRightOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { formatPipelineHealthLabel, type PipelineInsight } from '@/lib/pipelineInsights';
import type { MissionControlSummary } from '@/lib/missionControl';
import styles from '../dashboard.module.css';

interface Props {
  summary: MissionControlSummary;
  nextAction?: PipelineInsight;
  onRunAction: (item: PipelineInsight) => void;
  onAddApplication: () => void;
}

export default function MissionHeader({ summary, nextAction, onRunAction, onAddApplication }: Props) {
  const healthColor = summary.healthLabel === 'critical' ? 'red' : summary.healthLabel === 'watch' ? 'orange' : 'green';

  return (
    <section className={styles.missionHeader} aria-labelledby="mission-control-title">
      <div className={styles.missionHeaderText}>
        <div className={styles.commandEyebrow}>Mission Control</div>
        <h1 id="mission-control-title" className={styles.missionTitle}>
          本周求职作战台
        </h1>
        <p className={styles.missionHeadline}>{summary.headline}</p>
        <div className={styles.missionMeta}>
          <span className="op-tnum">
            {summary.weekStart} - {summary.weekEnd}
          </span>
          <Tag color={healthColor}>{formatPipelineHealthLabel(summary.healthLabel)}</Tag>
        </div>
      </div>

      <div className={styles.missionHeaderAction}>
        {nextAction ? (
          <Button
            type="primary"
            size="large"
            className={nextAction.priority === 'p0' ? styles.actionCta : undefined}
            icon={<ThunderboltOutlined />}
            onClick={() => onRunAction(nextAction)}
          >
            {nextAction.primaryAction.label}
            <ArrowRightOutlined />
          </Button>
        ) : (
          <Button type="primary" size="large" onClick={onAddApplication}>
            添加投递
            <ArrowRightOutlined />
          </Button>
        )}
      </div>
    </section>
  );
}

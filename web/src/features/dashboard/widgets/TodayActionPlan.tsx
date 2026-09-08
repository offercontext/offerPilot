import { Empty, Tag } from 'antd';
import { FireOutlined, FlagOutlined, ToolOutlined } from '@ant-design/icons';
import type { MissionActionGroups } from '@/lib/missionControl';
import type { PipelineInsight } from '@/lib/pipelineInsights';
import styles from '../dashboard.module.css';

interface Props {
  groups: MissionActionGroups;
  onAction: (item: PipelineInsight) => void;
  onSeeAll: () => void;
}

const GROUP_META = [
  { key: 'urgent', title: '紧急处理', icon: <FireOutlined />, tag: 'P0' },
  { key: 'prepare', title: '准备推进', icon: <ToolOutlined />, tag: '准备' },
  { key: 'momentum', title: '保持节奏', icon: <FlagOutlined />, tag: '节奏' },
] as const;

function priorityLabel(item: PipelineInsight): string {
  return item.priority.toUpperCase();
}

function evidenceHint(item: PipelineInsight): string {
  return item.evidence[0] ?? item.reason;
}

export default function TodayActionPlan({ groups, onAction, onSeeAll }: Props) {
  const total = groups.urgent.length + groups.prepare.length + groups.momentum.length;

  return (
    <section className={styles.todayActionPlan} aria-labelledby="today-action-plan-title">
      <div className={styles.sectionHeaderLine}>
        <div>
          <div className={styles.commandEyebrow}>Today</div>
          <h2 id="today-action-plan-title" className={styles.sectionHeading}>
            今日行动计划
          </h2>
        </div>
        <button type="button" className={styles.textButton} onClick={onSeeAll}>
          查看全部
        </button>
      </div>

      {total === 0 ? (
        <Empty
          className={styles.compactEmpty}
          description="暂无需要立即处理的行动。可以新增投递、整理材料，或进行一轮题目复习。"
        />
      ) : (
        <div className={styles.actionPlanGroups}>
          {GROUP_META.map((meta) => {
            const items = groups[meta.key];
            if (items.length === 0) return null;
            return (
              <div key={meta.key} className={styles.actionPlanGroup}>
                <div className={styles.actionPlanGroupTitle}>
                  <span aria-hidden="true">{meta.icon}</span>
                  <span>{meta.title}</span>
                  <Tag>{meta.tag}</Tag>
                </div>
                <div className={styles.actionPlanList}>
                  {items.slice(0, 4).map((item, index) => (
                    <button
                      key={item.id}
                      type="button"
                      className={`${styles.planActionRow} ${styles[item.priority]}`}
                      style={{ animationDelay: `${index * 40}ms` }}
                      onClick={() => onAction(item)}
                    >
                      <span className={`${styles.planPriority} op-tnum`}>{priorityLabel(item)}</span>
                      <span className={styles.planActionBody}>
                        <span className={styles.planActionTitle}>{item.title}</span>
                        <span className={styles.planActionReason}>{item.reason}</span>
                        <span className={styles.planActionEvidence}>{evidenceHint(item)}</span>
                      </span>
                      <span className={styles.planActionCta}>{item.primaryAction.label}</span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

import { Button, List, Space, Tag, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import type { ActionCommand, PipelineInsight } from '@/lib/pipelineInsights';
import styles from './pipeline.module.css';

type DetailAction = ActionCommand & { id?: string };

type DetailInsight = PipelineInsight & {
  impact?: string;
  secondaryActions?: DetailAction[];
};

interface Props {
  insight: PipelineInsight | null;
  open: boolean;
  onClose: () => void;
  onRunAction: (insight: PipelineInsight, actionId: string) => void;
}

const PRIORITY_LABEL: Record<PipelineInsight['priority'], string> = {
  p0: 'P0',
  p1: 'P1',
  p2: 'P2',
};

function getActionId(insight: PipelineInsight, action: DetailAction, kind: 'primary' | 'secondary') {
  return action.id ?? `${insight.id}:${kind}:${action.label}`;
}

export default function ActionDetailDrawer({ insight, open, onClose, onRunAction }: Props) {
  const detail = insight as DetailInsight | null;
  const primaryAction = detail?.primaryAction as DetailAction | undefined;
  const secondaryActions = detail?.secondaryActions ?? [];

  const title = detail?.title ? (
    <Space size={8} wrap>
      <Tag color={detail.priority === 'p0' ? 'red' : detail.priority === 'p1' ? 'orange' : 'blue'}>
        {PRIORITY_LABEL[detail.priority]}
      </Tag>
      <Typography.Text strong>{detail.title}</Typography.Text>
    </Space>
  ) : (
    '流程行动'
  );

  if (!open) return null;

  return (
    <section data-testid="action-detail-surface" className={`${styles.detailWorkspace} op-long-text`} aria-label="流程行动详情">
      {detail && primaryAction ? (
        <div className={styles.drawerBody}>
          <div className={styles.detailHeader}>
            <Button type="link" className={styles.backButton} icon={<ArrowLeftOutlined />} onClick={onClose}>
              返回工作台
            </Button>
            <div className={styles.detailTitle}>{title}</div>
          </div>
          <section className={styles.section}>
            <Typography.Title level={5}>为什么出现</Typography.Title>
            <Typography.Paragraph>{detail.reason}</Typography.Paragraph>
            {detail.evidence.length > 0 && (
              <List
                size="small"
                dataSource={detail.evidence}
                renderItem={(item) => <List.Item className="op-list-row">{item}</List.Item>}
              />
            )}
          </section>

          {detail.impact && (
            <section className={styles.section}>
              <Typography.Title level={5}>影响</Typography.Title>
              <Typography.Paragraph>{detail.impact}</Typography.Paragraph>
            </section>
          )}

          <section className={styles.section}>
            <Typography.Title level={5}>建议下一步</Typography.Title>
            <Space direction="vertical" size={12} className="op-control-actions">
              <Button type="primary" onClick={() => onRunAction(detail, getActionId(detail, primaryAction, 'primary'))}>
                {primaryAction.label}
              </Button>
              {secondaryActions.length > 0 && (
                <div className={styles.secondaryActions}>
                  {secondaryActions.map((action, index) => (
                    <Button
                      key={`${getActionId(detail, action, 'secondary')}:${index}`}
                      onClick={() => onRunAction(detail, getActionId(detail, action, 'secondary'))}
                    >
                      {action.label}
                    </Button>
                  ))}
                </div>
              )}
            </Space>
          </section>
        </div>
      ) : null}
    </section>
  );
}

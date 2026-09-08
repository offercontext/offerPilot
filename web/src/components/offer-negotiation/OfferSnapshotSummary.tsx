import { Card, Collapse, Descriptions } from 'antd';
import { OFFER_STATUS_LABELS, type OfferNegotiationSnapshot } from '@/types/offer';
import { SourceStateTag, type SourceState } from '../ui/SourceStateTag';
import styles from './OfferNegotiationPresentation.module.css';

interface Props {
  offer: OfferNegotiationSnapshot['offer_snapshot'];
  brief?: OfferNegotiationSnapshot['user_brief'];
  sourceState: Extract<SourceState, 'current' | 'frozen' | 'changed'>;
}

function formatMonthly(offer: OfferNegotiationSnapshot['offer_snapshot']): string {
  if (offer.base_monthly == null || offer.months_per_year == null) return '尚未填写';
  return `${offer.base_monthly / 1000}K × ${offer.months_per_year}`;
}

export default function OfferSnapshotSummary({ offer, brief, sourceState }: Props) {
  const fixedFacts = (
    <div data-section="fixed-facts">
      <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="公司">{offer.company_name}</Descriptions.Item>
      <Descriptions.Item label="职位">{offer.position_name}</Descriptions.Item>
      <Descriptions.Item label="状态">{OFFER_STATUS_LABELS[offer.status]}</Descriptions.Item>
      <Descriptions.Item label="月薪与月数">{formatMonthly(offer)}</Descriptions.Item>
      <Descriptions.Item label="签字费">{offer.signing_bonus == null ? '尚未填写' : offer.signing_bonus}</Descriptions.Item>
      <Descriptions.Item label="股权">{offer.equity || '尚未填写'}</Descriptions.Item>
      <Descriptions.Item label="福利">{offer.perks || '尚未填写'}</Descriptions.Item>
      <Descriptions.Item label="截止时间">{offer.deadline || '尚未填写'}</Descriptions.Item>
      <Descriptions.Item label="备注">{offer.notes || '尚未填写'}</Descriptions.Item>
      </Descriptions>
    </div>
  );

  const dimensionFacts = offer.dimensions.length > 0 ? (
    <div data-section="custom-dimensions">
      <strong>自定义比较维度</strong>
      <Descriptions column={1} size="small" bordered>
      {offer.dimensions.map((dimension) => (
        <Descriptions.Item key={dimension.path_id} label={dimension.label}>{dimension.value_text || '尚未填写'}</Descriptions.Item>
      ))}
      </Descriptions>
    </div>
  ) : null;

  const fullFacts = (
    <>
      {fixedFacts}
      {dimensionFacts}
    </>
  );

  return (
    <Card className={styles.snapshotCard} size="small">
      <div className={styles.snapshotHeader}>
        <div>
          <strong>{offer.company_name}</strong>
          <span>{offer.position_name}</span>
        </div>
        <SourceStateTag state={sourceState} detail={sourceState === 'frozen' ? '本次 AI 输入快照' : undefined} />
      </div>
      <div className={styles.snapshotFacts} data-section="fixed-facts-summary">
        <span>状态：{OFFER_STATUS_LABELS[offer.status]}</span>
        <span>月薪与月数：{formatMonthly(offer)}</span>
        <span data-testid="snapshot-signing-bonus">签字费：{offer.signing_bonus == null ? '尚未填写' : offer.signing_bonus}</span>
        <span>截止时间：{offer.deadline || '尚未填写'}</span>
      </div>
      {offer.dimensions.length > 0 && (
        <div className={styles.customDimensionSummary} data-section="custom-dimensions-summary">
          <strong>自定义比较维度</strong>
          {offer.dimensions.map((dimension) => (
            <span key={dimension.path_id}>{dimension.label}：{dimension.value_text || '尚未填写'}</span>
          ))}
        </div>
      )}
      {brief && (
        <div className={styles.briefSummary}>
          <span>本次目标：{brief.goal}</span>
          <span>本次顾虑：{brief.concerns}</span>
          <span>沟通场景：{brief.scenario}</span>
        </div>
      )}
      <Collapse
        ghost
        items={[{ key: 'facts', label: '查看完整来源', children: fullFacts }]}
      />
    </Card>
  );
}

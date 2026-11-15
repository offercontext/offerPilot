import { t, useLocale } from '@/i18n';
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
  if (offer.base_monthly == null || offer.months_per_year == null) return t("尚未填写");
  return `${offer.base_monthly / 1000}K × ${offer.months_per_year}`;
}

export default function OfferSnapshotSummary({ offer, brief, sourceState }: Props) {
  useLocale();
  const fixedFacts = (
    <div data-section="fixed-facts">
      <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label={t("公司")}>{offer.company_name}</Descriptions.Item>
      <Descriptions.Item label={t("职位")}>{offer.position_name}</Descriptions.Item>
      <Descriptions.Item label={t("状态")}>{OFFER_STATUS_LABELS[offer.status]}</Descriptions.Item>
      <Descriptions.Item label={t("月薪与月数")}>{formatMonthly(offer)}</Descriptions.Item>
      <Descriptions.Item label={t("签字费")}>{offer.signing_bonus == null ? t("尚未填写") : offer.signing_bonus}</Descriptions.Item>
      <Descriptions.Item label={t("股权")}>{offer.equity || t("尚未填写")}</Descriptions.Item>
      <Descriptions.Item label={t("福利")}>{offer.perks || t("尚未填写")}</Descriptions.Item>
      <Descriptions.Item label={t("截止时间")}>{offer.deadline || t("尚未填写")}</Descriptions.Item>
      <Descriptions.Item label={t("备注")}>{offer.notes || t("尚未填写")}</Descriptions.Item>
      </Descriptions>
    </div>
  );

  const dimensionFacts = offer.dimensions.length > 0 ? (
    <div data-section="custom-dimensions">
      <strong>{t("自定义比较维度")}</strong>
      <Descriptions column={1} size="small" bordered>
      {offer.dimensions.map((dimension) => (
        <Descriptions.Item key={dimension.path_id} label={dimension.label}>{dimension.value_text || t("尚未填写")}</Descriptions.Item>
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
        <SourceStateTag state={sourceState} detail={sourceState === 'frozen' ? t("本次 AI 输入快照") : undefined} />
      </div>
      <div className={styles.snapshotFacts} data-section="fixed-facts-summary">
        <span>{t("状态：")}{OFFER_STATUS_LABELS[offer.status]}</span>
        <span>{t("月薪与月数：")}{formatMonthly(offer)}</span>
        <span data-testid="snapshot-signing-bonus">{t("签字费：")}{offer.signing_bonus == null ? t("尚未填写") : offer.signing_bonus}</span>
        <span>{t("截止时间：")}{offer.deadline || t("尚未填写")}</span>
      </div>
      {offer.dimensions.length > 0 && (
        <div className={styles.customDimensionSummary} data-section="custom-dimensions-summary">
          <strong>{t("自定义比较维度")}</strong>
          {offer.dimensions.map((dimension) => (
            <span key={dimension.path_id}>{dimension.label}：{dimension.value_text || t("尚未填写")}</span>
          ))}
        </div>
      )}
      {brief && (
        <div className={styles.briefSummary}>
          <span>{t("本次目标：")}{brief.goal}</span>
          <span>{t("本次顾虑：")}{brief.concerns}</span>
          <span>{t("沟通场景：")}{brief.scenario}</span>
        </div>
      )}
      <Collapse
        ghost
        items={[{ key: 'facts', label: t("查看完整来源"), children: fullFacts }]}
      />
    </Card>
  );
}

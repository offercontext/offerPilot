import { t, useLocale } from '@/i18n';
import { Card, Tag, Button, Checkbox, Space, Typography } from 'antd';
import { EyeOutlined, LinkOutlined } from '@ant-design/icons';
import type { Offer } from '@/types/offer';
import { OFFER_STATUS_LABELS, OFFER_STATUS_COLORS } from '@/types/offer';
import { createPilotAttachmentDragBinding } from './PilotAttachmentHandle';
import { listOfferBindingState } from './offerWorkspaceModel';
import styles from './OfferCard.module.css';

const { Text } = Typography;

interface Props {
  offer: Offer;
  selectable?: boolean;
  selected: boolean;
  onToggleSelect: (id: number) => void;
  onCoach: (offer: Offer) => void;
  onNegotiation?: (offer: Offer) => void;
  emphasis?: 'primary' | 'secondary';
  onView: (offer: Offer) => void;
  applicationLinkState?: 'unbound' | 'available' | 'unavailable';
  onOpenApplication?: (applicationId: number) => void;
  onAttachToPilot?: (attachment: import('@/types/chat').PilotContextAttachment) => void;
}

function formatWan(n: number): string {
  return (n / 10000).toFixed(1) + t("万");
}

export default function OfferCard({ offer, selectable = true, selected, onToggleSelect, onCoach, onNegotiation, emphasis = 'primary', onView, applicationLinkState, onOpenApplication, onAttachToPilot }: Props) {
  useLocale();
  const bindingState = listOfferBindingState(offer);
  const resolvedApplicationLinkState = applicationLinkState ?? (bindingState === 'bound' ? 'available' : 'unbound');
  const historicalReadOnly = resolvedApplicationLinkState === 'unbound';
  const startPreparation = onNegotiation ?? onCoach;
  const offerDragBinding = onAttachToPilot
    ? createPilotAttachmentDragBinding({
        kind: 'offer',
        id: String(offer.id),
        label: `${offer.company_name} · ${offer.position_name}`,
      })
    : undefined;

  return (
    <Card
      size="small"
      className={styles.card}
      style={{ borderColor: OFFER_STATUS_COLORS[offer.status] }}
      {...offerDragBinding}
      title={
        <Space className={styles.heading}>
          {selectable ? <Checkbox aria-label={t("选择 Offer：{company_name}｜{position_name}", { company_name: offer.company_name, position_name: offer.position_name })} checked={selected} onChange={() => onToggleSelect(offer.id)} /> : null}
          <Text strong className={styles.company} title={offer.company_name}>{offer.company_name}</Text>
        </Space>
      }
      extra={<Tag color={OFFER_STATUS_COLORS[offer.status]}>{OFFER_STATUS_LABELS[offer.status]}</Tag>}
    >
      <div className={styles.position} title={offer.position_name}>{offer.position_name}</div>
      <div className={styles.salary}>
        {offer.base_monthly > 0 ? `${offer.base_monthly / 1000}K` : t("月薪待确认")}
        {offer.months_per_year > 0 ? t(" × {months_per_year} 薪", { months_per_year: offer.months_per_year }) : t(" · 年薪月数待确认")}
      </div>
      <div className={styles.facts}>{t("签字费 ")}{offer.signing_bonus == null ? t("尚未填写") : formatWan(offer.signing_bonus)}
        {offer.equity ? t(" · 期权 {equity}", { equity: offer.equity }) : ''}
        <br />
        {offer.total_cash > 0 ? t("年总包约 {value}", { value: formatWan(offer.total_cash) }) : t("年总包待确认")}
        {offer.deadline ? t(" · 截止 {deadline}", { deadline: offer.deadline }) : ''}
        {offer.application_id ? t(" · 关联投递 #{application_id}", { application_id: offer.application_id }) : t(" · 无关联投递")}
      </div>
      <div className={styles.actions}>
        {startPreparation && !historicalReadOnly && (
          <Button type={emphasis === 'primary' ? 'primary' : 'default'} data-action="start-negotiation" onClick={() => startPreparation(offer)}>{t("准备谈薪")}</Button>
        )}
        {resolvedApplicationLinkState === 'available' && offer.application_id && onOpenApplication ? (
          <Button type="link" data-action="open-application" icon={<LinkOutlined />} onClick={() => onOpenApplication(offer.application_id!)}>{t("返回所属投递")}</Button>
        ) : null}
        {resolvedApplicationLinkState === 'unavailable' && offer.application_id ? (
          <Button type="link" data-action="open-application" icon={<LinkOutlined />} disabled>{t("所属投递当前不可见")}</Button>
        ) : null}
        <Button data-action="view-offer" icon={<EyeOutlined />} onClick={() => onView(offer)}>
          {historicalReadOnly ? t("查看") : t("详情")}
        </Button>
      </div>
      {historicalReadOnly ? (
        <div role="note" data-binding-warning style={{ marginTop: 10, color: 'var(--op-warning, #b45309)', fontSize: 12, lineHeight: 1.5 }}>{t("历史 Offer 仅支持只读查看；不能编辑、准备谈薪或返回具体投递。")}</div>
      ) : null}
    </Card>
  );
}

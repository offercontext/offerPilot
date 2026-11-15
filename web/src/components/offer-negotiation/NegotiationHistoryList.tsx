import { t, useLocale, getLocale } from '@/i18n';
import type { OfferNegotiationProposal } from '@/types/offer';
import styles from './OfferNegotiationPresentation.module.css';

interface Props {
  items: OfferNegotiationProposal[];
  selectedId: number | null;
  onSelect: (item: OfferNegotiationProposal) => void;
}

export default function NegotiationHistoryList({ items, selectedId, onSelect }: Props) {
  useLocale();
  if (items.length === 0) return <p className={styles.muted}>{t("暂无已生成的谈薪准备记录。")}</p>;
  return (
    <div className={styles.historyList} aria-label={t("历史谈薪准备")}>
      {items.map((item) => (
        <button type="button" className={selectedId === item.id ? styles.historyItemSelected : styles.historyItem} key={item.id} onClick={() => onSelect(item)}>
          <span>{t("记录 #")}{item.id}</span>
          <span>{item.brief ? t("已确认") : t("未确认")}</span>
          {item.brief?.confirmed_at ? <time dateTime={item.brief.confirmed_at}>{new Intl.DateTimeFormat(getLocale(), { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(item.brief.confirmed_at))}</time> : null}
          {item.source_changed ? <span>{t("来源已变化")}</span> : null}
        </button>
      ))}
    </div>
  );
}

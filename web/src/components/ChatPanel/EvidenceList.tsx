import { t, useLocale } from '@/i18n';
import {
  BookOutlined,
  CalendarOutlined,
  DollarOutlined,
  FileTextOutlined,
  ProfileOutlined,
} from '@ant-design/icons';
import { createElement, useEffect, useId, useState } from 'react';
import {
  evidenceIdentity,
  evidenceSetIdentity,
  formatEvidenceMeta,
  type EvidenceItem,
  type EvidenceTarget,
} from './model';
import styles from './ChatPanel.module.css';

interface Props {
  items: EvidenceItem[];
  similar?: EvidenceItem[];
  remaining?: EvidenceItem[];
  remainingCount?: number;
  compact?: boolean;
  clamped?: boolean;
  onOpenEvidence?: (target: EvidenceTarget) => void;
}

const ICONS = {
  application: ProfileOutlined,
  event: CalendarOutlined,
  jd: FileTextOutlined,
  note: FileTextOutlined,
  knowledge: BookOutlined,
  offer: DollarOutlined,
  resume: FileTextOutlined,
  unknown: FileTextOutlined,
} satisfies Record<EvidenceItem['kind'], typeof FileTextOutlined>;

const TARGET_LABELS = {
  get application() { return t("投递"); },
  offer: 'Offer',
  get resume() { return t("简历"); },
  get event() { return t("日程"); },
} satisfies Record<EvidenceTarget['kind'], string>;

export default function EvidenceList({
  items,
  similar = [],
  remaining = similar,
  remainingCount,
  compact,
  clamped,
  onOpenEvidence,
}: Props) {
  useLocale();
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const setIdentity = evidenceSetIdentity(items, similar, remaining);
  const displayedItems = expanded ? [...items, ...remaining] : items;

  useEffect(() => {
    setExpanded(false);
  }, [setIdentity]);

  if (!items.length) return null;

  return (
    <div className={styles.evidenceGroup}>
      <ul
        id={listId}
        className={`${styles.evidenceList} ${compact ? styles.evidenceListCompact : ''} ${
          clamped ? styles.evidenceListClamped : ''
        }`}
        aria-label={t("参考依据")}
      >
        {displayedItems.map((item) => {
          const icon = ICONS[item.kind] ?? ICONS.unknown;
          const target = item.target;
          const title = item.localization?.title?.() ?? item.title;
          const meta = item.localization?.meta?.() ?? item.meta;
          const content = (
            <>
              <span className={styles.evidenceIcon} aria-hidden="true">
                {createElement(icon)}
              </span>
              <span className={styles.evidenceMain}>
                <span className={styles.evidenceTitle}>{title}</span>
                {meta ? <span className={styles.evidenceMeta} title={meta}>{formatEvidenceMeta(meta)}</span> : null}
                {(item.occurrences ?? 1) > 1 ? (
                  <span className={styles.evidenceCount} aria-label={t("同条依据出现 {occurrences} 次", { occurrences: item.occurrences })}>
                    ×{item.occurrences}
                  </span>
                ) : null}
                {item.snippet ? <span className={styles.evidenceSnippet}>{item.snippet}</span> : null}
              </span>
            </>
          );

          return (
            <li key={evidenceIdentity(item)} className={target && onOpenEvidence ? undefined : styles.evidenceItem}>
              {target && onOpenEvidence ? (
                <button
                  type="button"
                  className={styles.evidenceAction}
                  aria-label={t("打开{value}：{title}", { value: TARGET_LABELS[target.kind], title })}
                  onClick={() => onOpenEvidence(target)}
                >
                  {content}
                </button>
              ) : content}
            </li>
          );
        })}
      </ul>
      {remaining.length ? (
        <div className={`${styles.evidenceControls} ${compact ? styles.evidenceControlsCompact : ''}`}>
          <span className={styles.evidenceMore}>
            {similar.length ? t("另有 {count} 条同类依据", { count: similar.length }) : t("另有 {count} 条依据", { count: remaining.length })}
            {remainingCount && remainingCount > similar.length ? t("（共 {remainingCount} 条未展示）", { remainingCount: remainingCount }) : ''}
          </span>
          <button
            type="button"
            className={styles.evidenceExpand}
            aria-controls={listId}
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? t("收起依据") : t("展开依据")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

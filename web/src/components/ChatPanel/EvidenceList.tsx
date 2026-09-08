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
  application: '投递',
  offer: 'Offer',
  resume: '简历',
  event: '日程',
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
        aria-label="参考依据"
      >
        {displayedItems.map((item) => {
          const icon = ICONS[item.kind] ?? ICONS.unknown;
          const target = item.target;
          const content = (
            <>
              <span className={styles.evidenceIcon} aria-hidden="true">
                {createElement(icon)}
              </span>
              <span className={styles.evidenceMain}>
                <span className={styles.evidenceTitle}>{item.title}</span>
                {item.meta ? <span className={styles.evidenceMeta} title={item.meta}>{formatEvidenceMeta(item.meta)}</span> : null}
                {(item.occurrences ?? 1) > 1 ? (
                  <span className={styles.evidenceCount} aria-label={`同条依据出现 ${item.occurrences} 次`}>
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
                  aria-label={`打开${TARGET_LABELS[target.kind]}：${item.title}`}
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
            {similar.length ? `另有 ${similar.length} 条同类依据` : `另有 ${remaining.length} 条依据`}
            {remainingCount && remainingCount > similar.length ? `（共 ${remainingCount} 条未展示）` : ''}
          </span>
          <button
            type="button"
            className={styles.evidenceExpand}
            aria-controls={listId}
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? '收起依据' : '展开依据'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

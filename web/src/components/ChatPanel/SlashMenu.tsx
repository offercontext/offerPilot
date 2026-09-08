import { Fragment, createElement } from 'react';
import type { Capability } from './capabilities';
import styles from './ChatPanel.module.css';

interface Props {
  items: Capability[];
  selected: number;
  onSelect: (cap: Capability) => void;
  onHover: (index: number) => void;
}

export default function SlashMenu({ items, selected, onSelect, onHover }: Props) {
  if (!items.length) return null;
  let lastGroup = '';
  return (
    <div className={styles.slash} role="listbox" aria-label="能力命令">
      {items.map((cap, i) => {
        const showGroup = cap.group !== lastGroup;
        lastGroup = cap.group;
        return (
          <Fragment key={cap.id}>
            {showGroup && <div className={styles.slashGroup}>{cap.group}</div>}
            <div
              role="option"
              aria-selected={i === selected}
              className={`${styles.slashItem} ${i === selected ? styles.slashSel : ''}`}
              onMouseEnter={() => onHover(i)}
              onMouseDown={(e) => {
                e.preventDefault(); // keep textarea focus
                onSelect(cap);
              }}
            >
              <span className={styles.slashIcon} aria-hidden="true">
                {createElement(cap.icon)}
              </span>
              <span>
                <span className={styles.slashLabel}>{cap.label}</span>{' '}
                <span className={styles.slashHint}>— {cap.hint}</span>
              </span>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

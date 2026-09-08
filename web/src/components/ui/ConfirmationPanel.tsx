import type { ReactNode } from 'react';
import { SourceStateTag, type SourceState } from './SourceStateTag';
import styles from './ConfirmationPanel.module.css';

export interface ConfirmationSource {
  state: SourceState;
  detail?: string;
}

export interface ConfirmationPanelProps {
  title: string;
  description: string;
  sources?: ConfirmationSource[];
  children: ReactNode;
  className?: string;
}

export function ConfirmationPanel({ title, description, sources = [], children, className }: ConfirmationPanelProps) {
  return (
    <section className={[styles.panel, className].filter(Boolean).join(' ')} aria-label={title}>
      <div className={styles.copy}>
        <h3 className={styles.title}>{title}</h3>
        <p className={styles.description}>{description}</p>
      </div>
      {sources.length > 0 ? (
        <div className={styles.sources} aria-label="确认来源">
          {sources.map((source, index) => (
            <SourceStateTag key={`${source.state}-${source.detail ?? index}`} {...source} />
          ))}
        </div>
      ) : null}
      <div className={styles.actions}>{children}</div>
    </section>
  );
}

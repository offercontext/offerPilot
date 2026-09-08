import { useDroppable } from '@dnd-kit/core';
import type React from 'react';
import type { Application, ApplicationStatus } from '@/types/application';
import KanbanCard from './KanbanCard';
import styles from './KanbanBoard.module.css';

interface KanbanColumnProps {
  status: ApplicationStatus;
  label: string;
  color: string;
  cards: Application[];
  activeId: number | null;
  onOpenDetail?: (app: Application) => void;
  onRequestStatusChange: (app: Application, status: ApplicationStatus) => void;
}

export default function KanbanColumn({
  status,
  label,
  color,
  cards,
  activeId,
  onOpenDetail,
  onRequestStatusChange,
}: KanbanColumnProps) {
  const { isOver, setNodeRef } = useDroppable({ id: status });

  return (
    <div
      ref={setNodeRef}
      className={`${styles.column} ${isOver && activeId !== null ? styles.columnOver : ''}`}
      style={{ '--column-color': color } as React.CSSProperties}
    >
      <div className={styles.columnHeader}>
        <span className={styles.columnDot} style={{ background: color }} />
        <span className={styles.columnLabel}>{label}</span>
        <span className={styles.columnBadge}>{cards.length}</span>
      </div>
      <div className={styles.columnBody}>
        {cards.length === 0 ? (
          <div className={styles.emptyColumn}>暂无{label}的投递</div>
        ) : (
          cards.map((card) => (
            <KanbanCard
              key={card.id}
              record={card}
              isDragging={card.id === activeId}
              onOpenDetail={onOpenDetail}
              onRequestStatusChange={onRequestStatusChange}
            />
          ))
        )}
      </div>
    </div>
  );
}

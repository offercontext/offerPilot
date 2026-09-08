import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { getPilotRequest } from '@/services/chat';
import { forgetPendingStart, listPendingStarts, pendingStartsSnapshot, subscribePendingStarts } from '@/services/chatSubmission';
import styles from './ActionCard.module.css';

interface Props {
  busy: boolean;
  conversationId?: number;
  onOpen: (conversationId: number, options?: { refresh?: boolean }) => Promise<void>;
}

/** Persisted IDs recover display through GET only, including after a reload. */
export function PendingStartRecovery({ busy, conversationId, onOpen }: Props) {
  useSyncExternalStore(subscribePendingStarts, pendingStartsSnapshot, () => '[]');
  const pending = listPendingStarts();
  const [reading, setReading] = useState<string | null>(null);
  const [feedback, setFeedback] = useState('');
  const ownerEpoch = useRef(0);
  useEffect(() => {
    ownerEpoch.current += 1;
    setReading(null);
    setFeedback('');
    return () => { ownerEpoch.current += 1; };
  }, [conversationId, busy]);
  if (!pending.length || busy) return null;

  async function recover(requestId: string) {
    const epoch = ownerEpoch.current;
    setReading(requestId);
    setFeedback('正在读取已保存的任务…');
    try {
      const turn = await getPilotRequest(requestId);
      if (epoch !== ownerEpoch.current) return;
      await onOpen(turn.conversation_id, { refresh: true });
      if (['completed', 'failed', 'interrupted'].includes(turn.state)) {
        forgetPendingStart(requestId);
      } else {
        setFeedback('任务已接纳，尚无最终状态记录。已显示保存的消息，可稍后再次读取。');
      }
    } catch (error) {
      if (epoch !== ownerEpoch.current) return;
      const status = (error as { response?: { status?: number } })?.response?.status;
      if (status === 410) {
        forgetPendingStart(requestId);
        setFeedback('原对话已删除。');
      } else {
        setFeedback(status === 404 ? '暂未找到已接纳任务，请稍后再次查询。' : '任务暂时无法读取，请重试。');
      }
    } finally {
      if (epoch === ownerEpoch.current) setReading(null);
    }
  }

  return (
    <section className={styles.card} aria-label="恢复未确认的提交" aria-busy={reading !== null}>
      <div>有 {pending.length} 次提交的结果尚未确认。</div>
      <div className={styles.actions}>
        {pending.map((item, index) => (
          <button key={item.requestId} type="button" disabled={reading !== null} onClick={() => void recover(item.requestId)}>
            {reading === item.requestId ? '正在读取…' : `读取原任务${pending.length > 1 ? ` ${index + 1}` : ''}`}
          </button>
        ))}
      </div>
      {feedback ? <p className={styles.summary} role="status">{feedback}</p> : null}
    </section>
  );
}

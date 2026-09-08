import type { ReactNode } from 'react';
import type { ActionCommand, ActionPresentationV1 } from './contracts';
import styles from './ActionCard.module.css';

const decisionLabels = { undecided: '等待确认', approved: '原样批准', modified: '修改后批准', rejected: '已拒绝', cancelled: '已取消', expired: '已过期', not_applicable: '无需决定', unknown: '决定记录不完整' };
const executionLabels = { not_started: '尚未执行', running: '正在执行', committed: '已保存', failed: '执行失败', unknown: '执行结果待核对' };
const evidenceLabels = { verified: '依据已核实', incomplete: '过程记录不完整', unavailable: '来源暂不可用' };
const undoLabels = { unsupported: '', available: '可撤销', running: '正在撤销', undone: '已撤销', conflict: '撤销冲突', unknown: '撤销结果待核对' };
const commandLabels = { approve: '确认执行', modify: '修改后执行', reject: '拒绝本次操作', undo: '撤销本次操作', refresh: '刷新状态' };

export function supportedAction(action: ActionPresentationV1): boolean {
  return action.schema_version === 1
    && ['agent', 'product_action'].includes(action.source_kind)
    && [action.operation_id, action.source_revision, action.presentation_revision].every((value) => typeof value === 'string' && value.length > 0)
    && Array.isArray(action.available_actions)
    && action.available_actions.every((command) => Object.prototype.hasOwnProperty.call(commandLabels, command))
    && Object.prototype.hasOwnProperty.call(decisionLabels, action.decision) && Object.prototype.hasOwnProperty.call(executionLabels, action.execution)
    && Object.prototype.hasOwnProperty.call(evidenceLabels, action.evidence) && Object.prototype.hasOwnProperty.call(undoLabels, action.undo)
    && !(action.execution === 'committed' && action.decision === 'undecided')
    && !(['available', 'running', 'undone', 'conflict'].includes(action.undo) && action.execution !== 'committed')
    && !(['rejected', 'cancelled', 'expired'].includes(action.decision)
      && ['committed', 'running'].includes(action.execution));
}

export function ActionCard({ action, commands = {}, busy = false, children }: {
  action: ActionPresentationV1;
  commands?: Partial<Record<ActionCommand, () => void>>;
  busy?: boolean;
  children?: ReactNode;
}) {
  if (!supportedAction(action)) {
    return <section className={styles.card} role="status">操作展示暂不可用</section>;
  }
  return <section className={styles.card} aria-label={action.title} aria-busy={busy}
    data-operation-id={action.operation_id} data-execution={action.execution}>
    <div className={styles.heading}><strong>{action.title}</strong><span>{action.source_label}</span></div>
    {action.target ? <p className={styles.target}>{action.target}</p> : null}
    <div className={styles.states} role="status">
      <span>{decisionLabels[action.decision]}</span><b>{executionLabels[action.execution]}</b>
      <span>{evidenceLabels[action.evidence]}</span>
      {undoLabels[action.undo] ? <span>{undoLabels[action.undo]}</span> : null}
    </div>
    {action.summary ? <p className={styles.summary}>{action.summary}</p> : null}
    {action.available_actions.some((command) => typeof commands[command] === 'function') ? <div className={styles.actions}>
      {action.available_actions.map((command) => typeof commands[command] === 'function' ? <button key={command} type="button"
        disabled={busy} onClick={commands[command]}>{commandLabels[command]}</button> : null)}
    </div> : null}
    {children}
  </section>;
}

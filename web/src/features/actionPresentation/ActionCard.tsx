import { t, useLocale } from '@/i18n';
import type { ReactNode } from 'react';
import type { ActionCommand, ActionPresentationV1 } from './contracts';
import styles from './ActionCard.module.css';

const decisionLabels = { get undecided() { return t("等待确认"); }, get approved() { return t("原样批准"); }, get modified() { return t("修改后批准"); }, get rejected() { return t("已拒绝"); }, get cancelled() { return t("已取消"); }, get expired() { return t("已过期"); }, get not_applicable() { return t("无需决定"); }, get unknown() { return t("决定记录不完整"); } };
const executionLabels = { get not_started() { return t("尚未执行"); }, get running() { return t("正在执行"); }, get committed() { return t("已保存"); }, get failed() { return t("执行失败"); }, get unknown() { return t("执行结果待核对"); } };
const evidenceLabels = { get verified() { return t("依据已核实"); }, get incomplete() { return t("过程记录不完整"); }, get unavailable() { return t("来源暂不可用"); } };
const undoLabels = { unsupported: '', get available() { return t("可撤销"); }, get running() { return t("正在撤销"); }, get undone() { return t("已撤销"); }, get conflict() { return t("撤销冲突"); }, get unknown() { return t("撤销结果待核对"); } };
const commandLabels = { get approve() { return t("确认执行"); }, get modify() { return t("修改后执行"); }, get reject() { return t("拒绝本次操作"); }, get undo() { return t("撤销本次操作"); }, get refresh() { return t("刷新状态"); } };

// These are fixed server presentation labels, never user/AI content or targets.
const SERVER_UI_COPY = new Set([
  '保存准备重点', '准备重点', '确认后将其保存为下次准备重点。', '来源已变化，请刷新后重试。',
  '保存经历素材', '经历素材', '确认后将其保存到经历素材。', 'Pilot 操作',
  '操作状态暂时无法核实，请刷新后重试。', '提案已过期，请重新发起。', '待确认操作',
  '请核对内容后确认。', '操作已保存。', '操作未完成。', '已拒绝此操作。',
]);
function serverUiCopy(value: string): string { return SERVER_UI_COPY.has(value) ? t(value) : value; }

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
  useLocale();
  if (!supportedAction(action)) {
    return <section className={styles.card} role="status">{t("操作展示暂不可用")}</section>;
  }
  return <section className={styles.card} aria-label={serverUiCopy(action.title)} aria-busy={busy}
    data-operation-id={action.operation_id} data-execution={action.execution}>
    <div className={styles.heading}><strong>{serverUiCopy(action.title)}</strong><span>{serverUiCopy(action.source_label)}</span></div>
    {action.target ? <p className={styles.target}>{action.target}</p> : null}
    <div className={styles.states} role="status">
      <span>{decisionLabels[action.decision]}</span><b>{executionLabels[action.execution]}</b>
      <span>{evidenceLabels[action.evidence]}</span>
      {undoLabels[action.undo] ? <span>{undoLabels[action.undo]}</span> : null}
    </div>
    {action.summary ? <p className={styles.summary}>{serverUiCopy(action.summary)}</p> : null}
    {action.available_actions.some((command) => typeof commands[command] === 'function') ? <div className={styles.actions}>
      {action.available_actions.map((command) => typeof commands[command] === 'function' ? <button key={command} type="button"
        disabled={busy} onClick={commands[command]}>{commandLabels[command]}</button> : null)}
    </div> : null}
    {children}
  </section>;
}

import { t, useLocale } from '@/i18n';
import type { EvidenceTarget, ToolStep, TurnPresentation } from './model';
import ProcessTimeline from './ProcessTimeline';
import styles from './ChatPanel.module.css';

interface Props {
  title: string;
  steps: ToolStep[];
  presentation?: TurnPresentation;
  disabled: boolean;
  onAction: (action: string) => void;
  onOpenEvidence?: (target: EvidenceTarget) => void;
}

function completionStatus(steps: ToolStep[], presentation?: TurnPresentation): string {
  if (steps.length) return t("已完成 {count} 步", { count: steps.length });
  if (presentation) return t("已完成建议整理");
  return t("等待处理");
}

function normalizeActions(actions: string[]): string[] {
  const uniqueActions: string[] = [];
  const seen = new Set<string>();

  for (const action of actions) {
    const normalized = action.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    uniqueActions.push(normalized);
  }

  return uniqueActions;
}

export default function PilotTaskCard({
  title,
  steps,
  presentation,
  disabled,
  onAction,
  onOpenEvidence,
}: Props) {
  useLocale();
  const status = completionStatus(steps, presentation);
  const actions = normalizeActions(presentation?.actions ?? []);

  return (
    <article className={styles.taskCard} aria-label={t("本轮任务：{title}", { title: title })}>
      <header className={styles.taskHead}>
        <div>
          <span className={styles.taskLabel}>{t("本轮任务")}</span>
          <h3 className={styles.taskTitle}>{title}</h3>
        </div>
        <span className={styles.taskStatus}>{status}</span>
      </header>

      {steps.length ? <ProcessTimeline steps={steps} summary={status} embedded onOpenEvidence={onOpenEvidence} /> : null}

      {presentation ? (
        <section className={styles.taskConclusion} aria-label={t("结论")}>
          <h4>{t("结论")}</h4>
          <p>{presentation.conclusion}</p>
        </section>
      ) : null}

      {actions.length ? (
        <section className={styles.taskActions} aria-label={t("下一步")}>
          <h4>{t("下一步")}</h4>
          <div>
            {actions.map((action) => (
              <button
                key={action}
                type="button"
                aria-label={t("继续：{action}", { action: action })}
                disabled={disabled}
                onClick={() => onAction(action)}
              >
                {action}
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </article>
  );
}

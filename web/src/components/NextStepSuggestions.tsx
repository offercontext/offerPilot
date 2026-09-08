import type {
  NextStepDestination,
  NextStepSuggestions as Suggestions,
  ReadonlyDestination,
  SuggestionSessionState,
} from '@/lib/nextStepSuggestions';
import styles from './NextStepSuggestions.module.css';

export interface NextStepSuggestionsProps {
  applicationId: number;
  suggestions: Suggestions;
  sessionState: SuggestionSessionState | null;
  onSetDisposition: (applicationId: number, suggestionId: string, state: SuggestionSessionState | null) => void;
  onNavigate: (destination: NextStepDestination) => void;
  isNavigationAvailable?: (destination: NextStepDestination) => boolean;
  onNavigateReadonly?: (destination: ReadonlyDestination) => void;
  isReadonlyNavigationAvailable?: (destination: ReadonlyDestination) => boolean;
}

function isCurrentSessionState(
  state: SuggestionSessionState | null,
  stateKey: string,
): state is SuggestionSessionState {
  return Boolean(state && state.stateKey === stateKey);
}

export default function NextStepSuggestions({
  applicationId,
  suggestions,
  sessionState,
  onSetDisposition,
  onNavigate,
  isNavigationAvailable,
  onNavigateReadonly,
  isReadonlyNavigationAvailable,
}: NextStepSuggestionsProps) {
  const candidate = suggestions.candidates[0];
  const activeState = candidate && isCurrentSessionState(sessionState, candidate.stateKey) ? sessionState : null;
  const isSnoozed = activeState?.disposition === 'snoozed';
  const isIgnored = activeState?.disposition === 'ignored';
  const candidateNavigationAvailable = candidate
    ? isNavigationAvailable?.(candidate.destination) ?? true
    : false;

  return (
    <section className={styles.root} aria-label="下一步建议">
      {candidate && !isIgnored && !isSnoozed ? (
        <article className={styles.actionCard}>
          <div className={styles.eyebrow}>下一步建议</div>
          <h3>{candidate.title}</h3>
          <p>{candidate.reason}</p>
          <div className={styles.sources}>
            {candidate.sources.map((source) => (
              <span key={source.label} className={styles.sourceTag}>{source.label}</span>
            ))}
          </div>
          <div className={styles.actions}>
            <button
              type="button"
              disabled={!candidateNavigationAvailable}
              onClick={() => onNavigate(candidate.destination)}
            >前往</button>
            <button
              type="button"
              onClick={() => onSetDisposition(applicationId, candidate.id, {
                stateKey: candidate.stateKey,
                disposition: 'snoozed',
              })}
            >
              稍后处理
            </button>
            <button
              type="button"
              onClick={() => onSetDisposition(applicationId, candidate.id, {
                stateKey: candidate.stateKey,
                disposition: 'ignored',
              })}
            >
              忽略
            </button>
          </div>
        </article>
      ) : null}
      {candidate && isSnoozed ? (
        <div className={styles.collapsed}>
          <span>建议已暂时收起</span>
          <button type="button" onClick={() => onSetDisposition(applicationId, candidate.id, null)}>恢复建议</button>
        </div>
      ) : null}
      {suggestions.sourceRisks.length > 0 ? (
        <div className={styles.riskList}>
          {suggestions.sourceRisks.map((risk) => (
            <article className={styles.risk} data-testid="source-risk" key={risk.id}>
              <div>
                <strong>{risk.title}</strong>
                <p>{risk.reason}</p>
                <div className={styles.sources}>
                  {risk.sources.map((source) => (
                    <span key={source.label} className={styles.sourceTag}>{source.label}</span>
                  ))}
                </div>
              </div>
              {risk.readonlyDestination
                && onNavigateReadonly
                && (isReadonlyNavigationAvailable?.(risk.readonlyDestination) ?? false) ? (
                <button type="button" onClick={() => onNavigateReadonly(risk.readonlyDestination as ReadonlyDestination)}>
                  查看来源
                </button>
              ) : risk.readonlyDestination ? <span>暂不可打开</span> : null}
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

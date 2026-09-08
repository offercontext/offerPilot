import { useLayoutEffect, useState, createElement } from 'react';
import { RightOutlined, ThunderboltOutlined } from '@ant-design/icons';
import {
  remainingEvidence,
  selectEvidence,
  toolStepSetIdentity,
  type EvidenceTarget,
  type ToolStep,
} from './model';
import { toolMeta } from './capabilities';
import EvidenceList from './EvidenceList';
import styles from './ChatPanel.module.css';

interface Props {
  steps: ToolStep[];
  onOpenEvidence?: (target: EvidenceTarget) => void;
  summary?: string;
  embedded?: boolean;
}

export default function ProcessTimeline({ steps, onOpenEvidence, summary, embedded = false }: Props) {
  const [open, setOpen] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState(false);
  const stepSetIdentity = toolStepSetIdentity(steps);

  useLayoutEffect(() => {
    setExpandedSteps(false);
  }, [stepSetIdentity]);

  if (!steps.length) return null;

  const visibleSteps = expandedSteps ? steps : steps.slice(0, 8);
  const remainingSteps = Math.max(0, steps.length - visibleSteps.length);

  return (
    <div
      className={`${styles.timeline} ${open ? styles.timelineOpen : ''} ${embedded ? styles.timelineEmbedded : ''}`}
      aria-label="AI 操作摘要"
    >
      <button
        type="button"
        className={styles.tlHead}
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <ThunderboltOutlined aria-hidden="true" />
        <span>{summary ?? `AI 做了什么 · 共 ${steps.length} 步`}</span>
        <RightOutlined className={styles.tlChev} aria-hidden="true" />
      </button>
      {open ? (
        <div className={styles.tlBody}>
          <div className={styles.tlInner}>
            <ul className={styles.tlSteps}>
              {visibleSteps.map((step, index) => {
                const meta = toolMeta(step.name);
                const evidenceSelection = selectEvidence(step.evidence ?? [], 8);
                const evidenceCount = evidenceSelection.visible.length + evidenceSelection.remainingCount;
                return (
                  <li
                    key={`${step.toolCallId ?? step.name}-${index}`}
                    className={`${styles.step} ${meta.kind === 'write' ? styles.stepWrite : styles.stepRead}`}
                  >
                    <div className={styles.stepLine}>
                      <span className={styles.stepIcon} aria-hidden="true">
                        {createElement(meta.icon)}
                      </span>
                      <span className={styles.stepText}>
                        <b>{meta.label}</b>
                        {step.detail ? <span className={styles.stepDetail}> · {step.detail}</span> : null}
                      </span>
                      {evidenceCount ? <span className={styles.stepCount}>{evidenceCount} 条来源</span> : null}
                    </div>
                    {evidenceSelection.visible.length ? (
                      <EvidenceList
                        items={evidenceSelection.visible}
                        similar={evidenceSelection.similar}
                        remaining={remainingEvidence(step.evidence ?? [], evidenceSelection.visible)}
                        remainingCount={evidenceSelection.remainingCount}
                        compact
                        clamped
                        onOpenEvidence={onOpenEvidence}
                      />
                    ) : null}
                    {step.resultText ? <div className={styles.stepFallback}>工具返回：{step.resultText}</div> : null}
                    {step.evidenceUnavailable ? <div className={styles.stepFallback}>暂时无法展示这一步的明细。</div> : null}
                  </li>
                );
              })}
            </ul>
            {steps.length > 8 ? (
              <button
                type="button"
                className={styles.timelineExpand}
                aria-expanded={expandedSteps}
                onClick={() => setExpandedSteps((value) => !value)}
              >
                {expandedSteps ? '收起后续步骤' : `还有 ${remainingSteps} 步`}
              </button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

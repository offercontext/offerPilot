import type { Resume } from '@/types/resume';
import {
  auditResume,
  type ResumeAuditCategory,
  type ResumeAuditFinding,
  type ResumeAuditStatus,
} from '@/lib/resumeEvidenceAudit';
import { isSupplementableResumeFinding } from '@/lib/resumeFactSupplement';
import styles from './ResumeLibraryView.module.css';

interface Props {
  resume: Resume;
  onSupplement?: (finding: ResumeAuditFinding) => void;
}

const STATUS_ORDER: readonly ResumeAuditStatus[] = ['present', 'review', 'unknown'];
const CATEGORY_ORDER: readonly ResumeAuditCategory[] = ['structure', 'experience', 'facts', 'format'];

const STATUS_LABELS: Record<ResumeAuditStatus, string> = {
  present: '已具备',
  review: '建议检查',
  unknown: '无法判断',
};

const CATEGORY_LABELS: Record<ResumeAuditCategory, string> = {
  structure: '核心结构',
  experience: '经历内容',
  facts: '可补充事实',
  format: '版式能力边界',
};

export default function ResumeEvidenceAuditPanel({ resume, onSupplement }: Props) {
  const result = auditResume(resume);
  const findingsByCategory = groupFindings(result.findings);

  return (
    <article className={styles.auditPanel} aria-label="简历事实体检">
      <div className={styles.auditHeader}>
        <div>
          <div className={styles.auditTitle}>简历事实体检</div>
          <p className={styles.auditIntro}>
            只检查当前简历中可观察的信息，不会修改简历，也不会调用 AI。
          </p>
        </div>
      </div>

      {resume.parse_status === 'parse-failed' && (
        <p className={styles.auditParseNotice} role="status">
          原始文本解析失败；这里只能检查已经保存的结构化字段。
        </p>
      )}

      <div className={styles.auditSummary} aria-label="体检结果摘要">
        {STATUS_ORDER.map((status) => (
          <div key={status} className={styles.auditSummaryItem} data-audit-status={status}>
            <span className={`${styles.auditStatus} ${styles[`auditStatus${capitalize(status)}`]}`}>
              {STATUS_LABELS[status]}
            </span>
            <strong>{result.counts[status]}</strong>
          </div>
        ))}
      </div>

      {result.findings.length === 0 ? (
        <div className={styles.auditEmptyState}>暂无可展示的体检结果。</div>
      ) : (
        <div className={styles.auditCategories}>
          {CATEGORY_ORDER.map((category) => {
            const findings = findingsByCategory.get(category) ?? [];
            if (findings.length === 0) return null;
            const headingID = `resume-audit-category-${category}`;
            return (
              <section key={category} className={styles.auditCategory} aria-labelledby={headingID}>
                <h4 id={headingID} className={styles.auditCategoryTitle}>{CATEGORY_LABELS[category]}</h4>
                <div className={styles.auditFindings}>
                  {findings.map((finding) => (
                    <FindingDetails key={finding.id} finding={finding} onSupplement={onSupplement} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </article>
  );
}

function FindingDetails({
  finding,
  onSupplement,
}: {
  finding: ResumeAuditFinding;
  onSupplement?: (finding: ResumeAuditFinding) => void;
}) {
  const statusClass = styles[`auditStatus${capitalize(finding.status)}`];
  return (
    <details className={styles.auditFinding}>
      <summary className={styles.auditFindingSummary}>
        <span className={`${styles.auditStatus} ${statusClass}`} data-audit-status={finding.status}>
          {STATUS_LABELS[finding.status]}
        </span>
        <span>{finding.title}</span>
      </summary>
      <div className={styles.auditFindingBody}>
        <p>{finding.explanation}</p>
        {finding.source && (
          <div className={styles.auditSource}>
            <div className={styles.auditSourceLabel}>字段路径</div>
            <code className={styles.auditSourcePath}>{finding.source.path}</code>
            {finding.source.excerpt !== undefined && (
              <>
                <div className={styles.auditSourceLabel}>原文摘录</div>
                <blockquote className={styles.auditExcerpt}>{finding.source.excerpt}</blockquote>
              </>
            )}
          </div>
        )}
        {onSupplement && isSupplementableResumeFinding(finding) && (
          <button
            type="button"
            className={styles.auditSupplementButton}
            onClick={() => onSupplement(finding)}
          >
            补充真实事实
            <span aria-hidden="true">→</span>
          </button>
        )}
      </div>
    </details>
  );
}

function groupFindings(findings: ResumeAuditFinding[]): Map<ResumeAuditCategory, ResumeAuditFinding[]> {
  const groups = new Map<ResumeAuditCategory, ResumeAuditFinding[]>();
  for (const finding of findings) {
    const current = groups.get(finding.category) ?? [];
    current.push(finding);
    groups.set(finding.category, current);
  }
  return groups;
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

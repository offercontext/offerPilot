import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { Resume } from '@/types/resume';
import * as auditModule from '@/lib/resumeEvidenceAudit';
import ResumeEvidenceAuditPanel from './ResumeEvidenceAuditPanel';

function makeResume(content: unknown = {}, overrides: Partial<Resume> = {}): Resume {
  return {
    id: 1,
    name: '测试简历',
    file_path: '',
    parsed_data: '',
    parse_status: 'text-ready',
    title: '测试简历',
    is_master: true,
    parent_resume_id: null,
    source: 'manual',
    source_file_path: '',
    content_json: content as Resume['content_json'],
    deleted_at: null,
    created_at: '2026-08-06T00:00:00Z',
    completion_percent: 0,
    missing_sections: [],
    is_complete: false,
    ...overrides,
  };
}

describe('ResumeEvidenceAuditPanel', () => {
  it('renders Chinese explanations, counts, categories, and no ATS conclusion', () => {
    const markup = renderToStaticMarkup(
      <ResumeEvidenceAuditPanel
        resume={makeResume({
          contact: { name: '林晓' },
          experience: [{ highlights: ['负责订单服务', '负责订单服务'] }],
        })}
      />,
    );

    expect(markup).toContain('简历事实体检');
    expect(markup).toContain('已具备');
    expect(markup).toContain('建议检查');
    expect(markup).toContain('无法判断');
    expect(markup).toContain('核心结构');
    expect(markup).toContain('经历内容');
    expect(markup).toContain('可补充事实');
    expect(markup).toContain('版式能力边界');
    expect(markup).toContain('只检查当前简历中可观察的信息');
    expect(markup).toContain('不会修改简历');
    expect(markup).not.toContain('ATS 已通过');
    expect(markup).not.toContain('AI 优化');
    expect(markup).not.toContain('立即修复');
  });

  it('keeps excerpts collapsed and renders path plus original text in details', () => {
    const markup = renderToStaticMarkup(
      <ResumeEvidenceAuditPanel
        resume={makeResume({ experience: [{ highlights: ['负责订单服务'] }] })}
      />,
    );

    expect(markup).toContain('<details');
    expect(markup).not.toContain('<details open');
    expect(markup).toContain('/experience/0/highlights/0');
    expect(markup).toContain('负责订单服务');
    expect(markup).toContain('字段路径');
    expect(markup).toContain('原文摘录');
  });

  it('explains parse failure without claiming the whole resume passed', () => {
    const markup = renderToStaticMarkup(
      <ResumeEvidenceAuditPanel
        resume={makeResume({}, { parse_status: 'parse-failed' })}
      />,
    );

    expect(markup).toContain('只能检查已经保存的结构化字段');
    expect(markup).not.toContain('ATS 已通过');
  });

  it('renders text labels for each status instead of relying on color alone', () => {
    const markup = renderToStaticMarkup(
      <ResumeEvidenceAuditPanel
        resume={makeResume({
          contact: { name: '林晓' },
          education: [],
          experience: [{ highlights: ['负责订单服务'] }],
          career_intent: null,
        })}
      />,
    );

    expect(markup).toContain('data-audit-status="present"');
    expect(markup).toContain('data-audit-status="review"');
    expect(markup).toContain('data-audit-status="unknown"');
  });

  it('offers fact supplementation only when a review finding points to a saved string leaf', () => {
    const onSupplement = vi.fn();
    const markup = renderToStaticMarkup(
      <ResumeEvidenceAuditPanel
        resume={makeResume({ experience: [{ highlights: ['负责订单服务'] }] })}
        onSupplement={onSupplement}
      />,
    );

    expect(markup).toContain('补充真实事实');
    expect(markup.match(/补充真实事实/g)?.length).toBeGreaterThanOrEqual(1);
    expect(markup).not.toContain('针对版式补充真实事实');
  });

  it('renders the Chinese empty state when the audit has no findings', () => {
    const auditSpy = vi.spyOn(auditModule, 'auditResume').mockReturnValue({
      findings: [],
      counts: { present: 0, review: 0, unknown: 0 },
    });

    try {
      const markup = renderToStaticMarkup(
        <ResumeEvidenceAuditPanel resume={makeResume()} />,
      );
      expect(markup).toContain('暂无可展示的体检结果');
      expect(markup).not.toContain('ATS 已通过');
    } finally {
      auditSpy.mockRestore();
    }
  });

});

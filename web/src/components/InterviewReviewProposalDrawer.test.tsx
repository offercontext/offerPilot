import { describe, expect, it } from 'vitest';
import source from './InterviewReviewProposalDrawer.tsx?raw';

describe('InterviewReviewProposalDrawer', () => {
  it('requires confirmation, preserves history, and supports source-change regeneration', () => {
    expect(source).toContain('window.confirm');
    expect(source).toContain('listInterviewReviewProposals');
    expect(source).toContain('getInterviewReviewProposal');
    expect(source).toContain('createInterviewReviewProposal');
    expect(source).toContain('source_changed');
    expect(source).toContain('来源已变化');
    expect(source).toContain('重新生成复盘建议');
    expect(source).toContain('attemptState');
    expect(source).toContain('onAttemptStateChange');
    expect(source).toContain('handleClose');
    expect(source).not.toContain('const [attemptKey, setAttemptKey]');
  });

  it('keeps an unknown attempt owned by the parent across drawer unmounts', () => {
    expect(source).toContain('result_unknown: true');
    expect(source).toContain('onAttemptStateChange?.(null)');
    expect(source).toContain('if (generating &&');
  });

  it('ends the attempt after a successful response', () => {
    expect(source).toContain('onAttemptStateChange?.(null);');
    expect(source).toContain('event_id');
  });

  it('shows evidence labels and has no cross-domain write actions', () => {
    expect(source).toContain('复盘问题');
    expect(source).toContain('自我反思');
    expect(source).toContain('困难点');
    expect(source).toContain('情绪记录');
    expect(source).not.toContain('创建跟进');
    expect(source).not.toContain('开始练习');
    expect(source).not.toContain('保存为知识草稿');
  });
});

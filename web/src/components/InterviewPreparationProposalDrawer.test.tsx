import { describe, expect, it } from 'vitest';
import source from './InterviewPreparationProposalDrawer.tsx?raw';

describe('InterviewPreparationProposalDrawer', () => {
  it('renders the five Chinese sections and evidence source labels', () => {
    expect(source).toContain('准备方向');
    expect(source).toContain('经历故事提示');
    expect(source).toContain('建议复习的知识点');
    expect(source).toContain('可以向面试官确认的问题');
    expect(source).toContain('当前资料不足');
    expect(source).toContain('岗位描述');
    expect(source).toContain('选定简历');
    expect(source).toContain('已确认 Knowledge Evidence');
    expect(source).not.toContain('Generate interview preparation');
    expect(source).not.toContain('AI recommendation');
  });

  it('keeps the assertion privacy boundary and has no cross-domain writes', () => {
    expect(source).toContain('用户断言仅保存于本次快照，不会发送给 AI');
    expect(source).not.toContain('创建题目');
    expect(source).not.toContain('创建提醒');
    expect(source).not.toContain('写入 Memory');
  });

  it('persists the complete draft above the drawer for remount recovery', () => {
    expect(source).toContain('onDraftChange');
    expect(source).toContain('draft?.attemptState.key');
    expect(source).toContain('draft?.jdText');
    expect(source).toContain('draft?.knowledgeSelections');
  });
});

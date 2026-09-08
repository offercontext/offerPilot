import { describe, expect, it } from 'vitest';
import source from './InterviewKnowledgeCaptureDrawer.tsx?raw';

describe('InterviewKnowledgeCaptureDrawer', () => {
  it('keeps raw fragments separate from editable preview and requires confirmation', () => {
    expect(source).toContain('直接保存选中原文');
    expect(source).toContain('生成笔记预览');
    expect(source).toContain('用户选中的原始片段');
    expect(source).toContain('AI 笔记预览');
    expect(source).toContain('可编辑笔记预览');
    expect(source).toContain('知识笔记标题');
    expect(source).toContain('证据引用');
    expect(source).toContain('window.confirm');
    expect(source).toContain('confirmInterviewKnowledgeCapture');
    expect(source).not.toContain('createQuestion');
    expect(source).not.toContain('createMemory');
  });

  it('handles safe empty preview and unknown delete result without leaking raw errors', () => {
    expect(source).toContain('暂无可验证的笔记预览');
    expect(source).toContain('操作结果未知，请重新打开复盘确认状态');
    expect(source).toContain('capture_attempt_confirmed');
    expect(source).toContain("previewStatus: 'confirm_unknown'");
    expect(source).toContain("draft.previewStatus === 'confirm_unknown'");
    expect(source).not.toContain('error.message');
    expect(source).not.toContain('error.response?.data?.error');
  });

  it('does not claim a frozen source before canonical fragments exist', () => {
    expect(source).toContain('draft.canonicalFragments.length > 0');
    expect(source).not.toContain('<SourceStateTag state="frozen" detail="仅保存所选面试原始片段" />');
  });
});

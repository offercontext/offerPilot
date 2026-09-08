import { describe, expect, it } from 'vitest';
import source from './AppShell.tsx?raw';

describe('AppShell interview knowledge capture ownership', () => {
  it('owns capture drafts by note id and preserves unknown attempts across drawer remount', () => {
    expect(source).toContain('interviewKnowledgeCaptureDrafts');
    expect(source).toContain('noteId');
    expect(source).toContain('Record<number, InterviewKnowledgeCaptureDraft>');
    expect(source).toContain('onInterviewKnowledgeCaptureDraftChange');
    expect(source).toContain('clearInterviewKnowledgeCaptureDraft');
  });

  it('clears ordinary cancellation but does not delete unknown-result drafts locally', () => {
    expect(source).toContain('clearInterviewKnowledgeCaptureDraft');
    expect(source).toContain('onInterviewKnowledgeCaptureNoteChanged');
  });
});

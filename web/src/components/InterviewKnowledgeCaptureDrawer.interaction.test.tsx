import { describe, expect, it } from 'vitest';
import source from './InterviewKnowledgeCaptureDrawer.tsx?raw';

async function loadTokens(): Promise<string> {
  const fsModule = 'node:fs';
  const { readFileSync } = (await import(fsModule)) as {
    readFileSync: (path: URL, encoding: string) => string;
  };
  return readFileSync(new URL('../theme/tokens.css', import.meta.url), 'utf8');
}

describe('InterviewKnowledgeCaptureDrawer interaction contract', () => {
  it('uses parent-owned draft state and keeps unknown results on close', () => {
    expect(source).toContain('draft: InterviewKnowledgeCaptureDraft');
    expect(source).toContain('onDraftChange');
    expect(source).toContain("draft.previewStatus === 'provider_unknown'");
    expect(source).toContain('deleteUnconfirmedInterviewKnowledgeAttempt');
    expect(source).toContain('resultUnknown');
    expect(source).toContain('data-testid="knowledge-capture-source-panel"');
  });

  it('lets long evidence tags wrap inside the drawer', async () => {
    const tokens = await loadTokens();

    expect(source).toContain('className="op-long-text"');
    expect(tokens).toContain('.op-long-text.ant-tag');
    expect(tokens).toContain('white-space: normal');
  });
});

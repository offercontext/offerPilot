import { describe, expect, it } from 'vitest';
import source from './ApplicationDetail.tsx?raw';

describe('ApplicationDetail opportunity fit boundary', () => {
  it('does not expose the legacy URL JD analysis or external job link', () => {
    expect(source).not.toContain('analyzeJD');
    expect(source).not.toContain('JDAnalyzeModal');
    expect(source).not.toContain('application.job_url');
  });
});

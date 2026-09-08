import { describe, expect, it } from 'vitest';
import source from './resumes.ts?raw';

describe('resume service v0.1 contract', () => {
  it('exposes structured create/sample/patch/copy calls without a download helper', () => {
    expect(source).toContain('/resumes/from-sample');
    expect(source).toContain('patch<Resume>');
    expect(source).toContain('/copy');
    expect(source).not.toContain('updateResumeText');
    expect(source).not.toContain('downloadResumeFile');
    expect(source).not.toContain('/file');
  });
});

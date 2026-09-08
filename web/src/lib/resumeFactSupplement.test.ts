import { describe, expect, it } from 'vitest';
import type { ResumeAuditFinding } from './resumeEvidenceAudit';
import {
  applyResumeFactSupplement,
  isSupplementableResumeFinding,
  validateSupplementText,
} from './resumeFactSupplement';

const finding = (overrides: Partial<ResumeAuditFinding> = {}): ResumeAuditFinding => ({
  id: 'facts-quantification',
  category: 'facts',
  status: 'review',
  title: '可补充真实事实',
  explanation: '如有真实数据，可以补充。',
  source: { path: '/experience/0/highlights/0', excerpt: '负责订单服务' },
  ...overrides,
});

describe('isSupplementableResumeFinding', () => {
  it('only accepts review findings with a concrete string-leaf excerpt', () => {
    expect(isSupplementableResumeFinding(finding())).toBe(true);
    expect(isSupplementableResumeFinding(finding({ status: 'present' }))).toBe(false);
    expect(isSupplementableResumeFinding(finding({ source: { path: '/experience/0' } }))).toBe(false);
    expect(isSupplementableResumeFinding(finding({ source: { path: '/experience/0', excerpt: '' } }))).toBe(true);
  });
});

describe('applyResumeFactSupplement', () => {
  it('replaces the exact string leaf without mutating the original content', () => {
    const original = { experience: [{ highlights: ['负责订单服务'] }] };
    const result = applyResumeFactSupplement(
      original,
      '/experience/0/highlights/0',
      '负责订单服务',
      '负责订单服务治理，将平均响应时间从 320ms 降至 180ms',
    );

    expect(result).toEqual({
      experience: [{ highlights: ['负责订单服务治理，将平均响应时间从 320ms 降至 180ms'] }],
    });
    expect(original.experience[0].highlights[0]).toBe('负责订单服务');
  });

  it('decodes RFC 6901 segments and accepts canonical array indexes', () => {
    const result = applyResumeFactSupplement(
      { 'project/~': [{ 'a/b': '旧表述' }] },
      '/project~1~0/0/a~1b',
      '旧表述',
      '新表述',
    );
    expect(result).toEqual({ 'project/~': [{ 'a/b': '新表述' }] });
  });

  it.each([
    '/experience/00/highlights/0',
    '/experience/-1/highlights/0',
    '/experience/0/highlights/01',
    '/experience/0/highlights/-',
    '/experience/0/highlights/0~2',
    '/__proto__/polluted',
    '/constructor/prototype',
  ])('rejects an unsafe or non-canonical path: %s', (path) => {
    expect(() => applyResumeFactSupplement(
      { experience: [{ highlights: ['旧表述'] }] },
      path,
      '旧表述',
      '新表述',
    )).toThrow();
  });

  it('rejects a stale excerpt and a non-string target', () => {
    expect(() => applyResumeFactSupplement(
      { experience: [{ highlights: ['已变化'] }] },
      '/experience/0/highlights/0',
      '旧表述',
      '新表述',
    )).toThrow('原文已变化');
    expect(() => applyResumeFactSupplement(
      { experience: [{ highlights: [42] }] },
      '/experience/0/highlights/0',
      '42',
      '新表述',
    )).toThrow('字符串');
  });

  it('fails closed for accessors, sparse arrays, deep data, and oversized data', () => {
    const accessor: Record<string, unknown> = {};
    Object.defineProperty(accessor, 'experience', { enumerable: true, get: () => [] });
    expect(() => applyResumeFactSupplement(accessor, '/experience/0', '', '新表述')).toThrow();

    const sparse = { experience: new Array(2) };
    expect(() => applyResumeFactSupplement(sparse, '/experience/0', '', '新表述')).toThrow();

    let deep: unknown = '旧表述';
    let path = '';
    for (let index = 0; index < 130; index += 1) {
      deep = { next: deep };
      path += '/next';
    }
    expect(() => applyResumeFactSupplement(deep, path, '旧表述', '新表述')).toThrow();

    expect(() => applyResumeFactSupplement(
      { experience: Array.from({ length: 10_100 }, () => '旧表述') },
      '/experience/0',
      '旧表述',
      '新表述',
    )).toThrow();
  });
});

describe('validateSupplementText', () => {
  it('uses Unicode code points and trims only the outer whitespace', () => {
    expect(validateSupplementText('  完成迁移 🚀  ')).toBe('完成迁移 🚀');
    expect(validateSupplementText('🚀'.repeat(400))).toBe('🚀'.repeat(400));
    expect(() => validateSupplementText('')).toThrow('不能为空');
    expect(() => validateSupplementText('🚀'.repeat(401))).toThrow('400');
  });
});

import { describe, expect, it } from 'vitest';
import type { Resume } from '@/types/resume';
import { auditResume } from './resumeEvidenceAudit';

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

describe('auditResume', () => {
  it('returns stable counts and the fixed format boundary finding', () => {
    const result = auditResume(makeResume({ contact: { email: 'ada@example.com' } }));

    expect(result.findings[result.findings.length - 1]).toMatchObject({
      id: 'format-visual-unknown',
      category: 'format',
      status: 'unknown',
    });
    expect(result.counts).toEqual({
      present: expect.any(Number),
      review: expect.any(Number),
      unknown: expect.any(Number),
    });
    expect(result.counts.unknown).toBeGreaterThan(0);
  });

  it('marks core fields present, review, or unknown without requiring optional sections', () => {
    const result = auditResume(makeResume({
      contact: { name: 'Ada' },
      education: [],
      experience: [{ highlights: ['Built APIs'] }],
      projects: undefined,
      skills: 'TypeScript',
      career_intent: 'invalid',
    }));

    expect(result.findings).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'structure-contact', status: 'present' }),
      expect.objectContaining({ id: 'structure-education', status: 'review' }),
      expect.objectContaining({ id: 'structure-experience', status: 'present' }),
      expect.objectContaining({ id: 'structure-projects', status: 'review' }),
      expect.objectContaining({ id: 'structure-skills', status: 'present' }),
      expect.objectContaining({ id: 'structure-career-intent', status: 'unknown' }),
    ]));
  });

  it.each([
    ['contact', undefined, 'review'],
    ['contact', null, 'unknown'],
    ['contact', {}, 'review'],
    ['contact', '   ', 'unknown'],
    ['contact', { profile: { label: '后端工程师' } }, 'present'],
    ['contact', { name: '筱哲', email: '' }, 'present'],
    ['contact', { name: 'Ada', phone: null }, 'unknown'],
    ['education', undefined, 'review'],
    ['education', null, 'unknown'],
    ['education', [], 'review'],
    ['education', '   ', 'unknown'],
    ['education', ['   '], 'review'],
    ['education', [{ school: '示例大学' }], 'present'],
    ['education', [{ school: '示例大学' }, null], 'unknown'],
    ['education', [{ school: '示例大学' }, []], 'unknown'],
    ['education', [[{ school: '示例大学' }]], 'unknown'],
    ['experience', undefined, 'review'],
    ['experience', null, 'unknown'],
    ['experience', [], 'review'],
    ['experience', '   ', 'unknown'],
    ['experience', ['   '], 'review'],
    ['experience', ['负责订单服务'], 'present'],
    ['experience', [{ highlights: ['负责订单服务'] }], 'present'],
    ['experience', [{ bullets: ['负责订单服务'] }], 'present'],
    ['experience', [{ achievements: ['负责订单服务'] }], 'present'],
    ['experience', [{ highlights: ['有效内容', null] }], 'unknown'],
    ['projects', undefined, 'review'],
    ['projects', null, 'unknown'],
    ['projects', [], 'review'],
    ['projects', '   ', 'unknown'],
    ['projects', ['   '], 'review'],
    ['projects', [{ name: '示例项目' }], 'present'],
    ['projects', [{ name: '示例项目' }, {}], 'unknown'],
    ['projects', [[{ name: '示例项目' }]], 'unknown'],
    ['skills', undefined, 'review'],
    ['skills', null, 'unknown'],
    ['skills', {}, 'review'],
    ['skills', [], 'review'],
    ['skills', '   ', 'review'],
    ['skills', 'TypeScript', 'present'],
    ['skills', [{ label: 'TypeScript' }], 'present'],
    ['skills', [{ label: 'TypeScript' }, null], 'unknown'],
    ['career_intent', undefined, 'review'],
    ['career_intent', null, 'unknown'],
    ['career_intent', {}, 'review'],
    ['career_intent', '   ', 'unknown'],
    ['career_intent', { target_roles: ['前端工程师'] }, 'present'],
    ['career_intent', { target_roles: ['前端工程师'], target_locations: [null] }, 'unknown'],
  ] as const)('classifies %s=%j as %s', (field, value, status) => {
    const result = auditResume(makeResume({ [field]: value }));
    const findingID = field === 'career_intent' ? 'structure-career-intent' : `structure-${field}`;
    expect(result.findings.find((item) => item.id === findingID)?.status).toBe(status);
  });

  it('uses all three experience bullet keys and preserves a direct string entry path', () => {
    const result = auditResume(makeResume({
      experience: [
        '直接经历要点',
        { highlights: ['highlight 要点'] },
        { bullets: ['bullet 要点'] },
        { achievements: ['achievement 要点'] },
      ],
    }));

    expect(result.findings.find((item) => item.id === 'facts-quantification')?.source).toMatchObject({
      path: '/experience/0',
      excerpt: '直接经历要点',
    });
    expect(result.findings.filter((item) => item.id === 'experience-bullets-unknown')).toHaveLength(0);
  });

  it('detects blank, duplicate, and overlong bullets with stable evidence paths', () => {
    const result = auditResume(makeResume({
      experience: [{ highlights: ['  ', 'Built APIs', 'Built APIs', '🚀'.repeat(241)] }],
    }));

    expect(result.findings).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: 'experience-empty-bullet',
        status: 'review',
        source: { path: '/experience/0/highlights/0', excerpt: '  ', fullText: '  ' },
      }),
      expect.objectContaining({
        id: 'experience-duplicate-bullet',
        status: 'review',
        source: { path: '/experience/0/highlights/2', excerpt: 'Built APIs', fullText: 'Built APIs' },
      }),
      expect.objectContaining({
        id: 'experience-long-bullet',
        status: 'review',
        source: expect.objectContaining({ path: '/experience/0/highlights/3' }),
      }),
    ]));
  });

  it('only offers a truthful-data prompt when recognized bullets contain no Arabic digits', () => {
    const review = auditResume(makeResume({ experience: [{ highlights: ['  ', 'Improved reliability'] }] }));
    const present = auditResume(makeResume({ experience: [{ highlights: ['Improved reliability by 20%'] }] }));

    expect(review.findings).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: 'facts-quantification',
        status: 'review',
        source: {
          path: '/experience/0/highlights/1',
          excerpt: 'Improved reliability',
          fullText: 'Improved reliability',
        },
        explanation: expect.stringContaining('如有真实数据，可以补充'),
      }),
    ]));
    const presentFinding = present.findings.find((item) => item.id === 'facts-quantification');
    expect(presentFinding).toMatchObject({ id: 'facts-quantification', status: 'present' });
    expect(presentFinding?.explanation).toContain('不代表真实或充分');
    expect(presentFinding?.explanation).not.toMatch(/估算|范围|必须量化/);
  });

  it('omits the truthful-data prompt when every recognized bullet is blank', () => {
    const result = auditResume(makeResume({ experience: [{ highlights: ['  ', String.fromCharCode(9)] }] }));

    expect(result.findings.find((item) => item.id === 'facts-quantification')).toBeUndefined();
  });

  it('does not treat raw_text as structured experience and reports malformed shapes as unknown', () => {
    const result = auditResume(makeResume({
      raw_text: 'Built APIs',
      experience: [{ highlights: [null, 7, { text: 'not a bullet' }] }],
    }));

    expect(result.findings).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'experience-bullets-unknown', status: 'unknown' }),
    ]));
    expect(result.findings.find((item) => item.id === 'facts-quantification')).toBeUndefined();
  });

  it('reports a review finding when experience items have no recognized bullet collection', () => {
    const result = auditResume(makeResume({ experience: [{ company: '示例科技', title: '工程师' }] }));

    expect(result.findings).toEqual(expect.arrayContaining([
      expect.objectContaining({
        id: 'experience-bullets-missing',
        category: 'experience',
        status: 'review',
        source: { path: '/experience/0' },
      }),
    ]));
  });

  it('reports the first experience item without recognized bullets', () => {
    const result = auditResume(makeResume({
      experience: [
        { highlights: ['Built APIs'] },
        { company: 'Example Co', title: 'Engineer' },
        { achievements: ['Reduced latency'] },
      ],
    }));

    expect(result.findings.filter((item) => item.id === 'experience-bullets-missing')).toEqual([
      expect.objectContaining({ source: { path: '/experience/1' } }),
    ]);
  });

  it('uses exact 240/241 code-point boundaries and truncates excerpts at 160 code points', () => {
    const bullet240 = '界'.repeat(240);
    const bullet241 = '界'.repeat(241);
    const atLimit = auditResume(makeResume({ experience: [{ highlights: [bullet240] }] }));
    const overLimit = auditResume(makeResume({ experience: [{ highlights: [bullet241] }] }));

    expect(atLimit.findings.find((item) => item.id === 'experience-long-bullet')).toBeUndefined();
    const longFinding = overLimit.findings.find((item) => item.id === 'experience-long-bullet');
    expect(longFinding).toMatchObject({ status: 'review' });
    expect(longFinding?.source?.excerpt).toBe(`${'界'.repeat(160)}…`);
    expect(Array.from(longFinding?.source?.excerpt ?? '')).toHaveLength(161);
  });

  it('truncates 241 emoji code points without splitting surrogate pairs', () => {
    const bullet = '🚀'.repeat(241);
    const result = auditResume(makeResume({ experience: [{ highlights: [bullet] }] }));
    const longFinding = result.findings.find((item) => item.id === 'experience-long-bullet');

    expect(result.findings.filter((item) => item.id === 'experience-long-bullet')).toHaveLength(1);
    expect(longFinding?.source?.excerpt).toBe(`${'🚀'.repeat(160)}…`);
    expect(Array.from(longFinding?.source?.excerpt ?? '')).toHaveLength(161);
  });

  it('truncates 242 combining-mark code points without Unicode normalization', () => {
    const bullet = 'e\u0301'.repeat(121);
    const result = auditResume(makeResume({ experience: [{ highlights: [bullet] }] }));
    const longFinding = result.findings.find((item) => item.id === 'experience-long-bullet');

    expect(result.findings.filter((item) => item.id === 'experience-long-bullet')).toHaveLength(1);
    expect(longFinding?.source?.excerpt).toBe(`${'e\u0301'.repeat(80)}…`);
    expect(longFinding?.source?.excerpt).not.toContain('é');
    expect(longFinding?.source?.fullText).toBe(bullet);
  });

  it('keeps the full saved leaf beside a 160-code-point display preview', () => {
    const bullet = '界'.repeat(241);
    const result = auditResume(makeResume({ experience: [{ highlights: [bullet] }] }));
    const finding = result.findings.find((item) => item.id === 'facts-quantification');

    expect(finding?.source).toEqual({
      path: '/experience/0/highlights/0',
      excerpt: `${'界'.repeat(160)}…`,
      fullText: bullet,
    });
  });

  it('preserves CJK, emoji, combining marks, and newlines in excerpts below the limit', () => {
    const excerpt = '中文 🚀 e\u0301\n保留原文';
    const result = auditResume(makeResume({ experience: [{ highlights: [excerpt] }] }));

    expect(result.findings.find((item) => item.id === 'facts-quantification')?.source?.excerpt).toBe(excerpt);
  });

  it('keeps the complete finding ID order stable when every rule is triggered', () => {
    const result = auditResume(makeResume({
      contact: {},
      education: [],
      experience: ['  ', '重复要点', '重复要点', '界'.repeat(241), null],
      projects: [],
      skills: [],
      career_intent: {},
    }));

    expect(result.findings.map((item) => item.id)).toEqual([
      'structure-contact',
      'structure-education',
      'structure-experience',
      'structure-projects',
      'structure-skills',
      'structure-career-intent',
      'experience-empty-bullet',
      'experience-duplicate-bullet',
      'experience-long-bullet',
      'experience-bullets-unknown',
      'facts-quantification',
      'format-visual-unknown',
    ]);
  });

  it('does not treat raw input as mutable and is deterministic for the same input', () => {
    const resume = makeResume({ experience: [{ highlights: ['Built APIs'] }] });
    const before = structuredClone(resume);

    const first = auditResume(resume);
    const second = auditResume(resume);

    expect(resume).toEqual(before);
    expect(second).toEqual(first);
    expect(JSON.stringify(second)).not.toMatch(/Date|random|Math/);
  });

  it('returns an unknown content finding instead of throwing for invalid content', () => {
    for (const content of [null, [], 'invalid', 42, { experience: 'invalid' }]) {
      expect(() => auditResume(makeResume(content))).not.toThrow();
    }
    expect(auditResume(makeResume(null)).findings).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'structure-content-json', status: 'unknown' }),
    ]));
  });

  it.each([
    ['contact', { score: Number.NaN }],
    ['education', [{ score: Number.POSITIVE_INFINITY }]],
    ['experience', [{ highlights: [() => 'not a bullet'] }]],
    ['projects', [{ token: Symbol('invalid') }]],
    ['skills', [Number.NaN]],
    ['career_intent', { target_roles: [Number.NEGATIVE_INFINITY] }],
  ] as const)('does not treat %s special runtime values as present', (field, value) => {
    expect(() => auditResume(makeResume({ [field]: value }))).not.toThrow();
    const result = auditResume(makeResume({ [field]: value }));

    const findingID = field === 'career_intent' ? 'structure-career-intent' : `structure-${field}`;
    expect(result.findings.find((item) => item.id === findingID)?.status).toBe('unknown');
  });

  it('returns unknown instead of throwing for a cyclic core field object', () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;

    expect(() => auditResume(makeResume({ contact: cyclic }))).not.toThrow();
    const result = auditResume(makeResume({ contact: cyclic }));

    expect(result.findings.find((item) => item.id === 'structure-contact')?.status).toBe('unknown');
  });

  it('does not throw for throwing runtime getters and marks affected fields unknown', () => {
    const throwingContentResume = makeResume({ contact: { name: 'Ada' } });
    Object.defineProperty(throwingContentResume, 'content_json', {
      configurable: true,
      get() {
        throw new Error('content getter failed');
      },
    });

    expect(() => auditResume(throwingContentResume)).not.toThrow();
    expect(auditResume(throwingContentResume).findings[0]).toMatchObject({
      id: 'structure-content-json',
      status: 'unknown',
    });

    const throwingFieldResume = makeResume({ contact: { name: 'Ada' } });
    Object.defineProperty(throwingFieldResume.content_json, 'experience', {
      configurable: true,
      get() {
        throw new Error('experience getter failed');
      },
    });

    expect(() => auditResume(throwingFieldResume)).not.toThrow();
    expect(auditResume(throwingFieldResume).findings).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'structure-experience', status: 'unknown' }),
      expect.objectContaining({ id: 'experience-bullets-unknown', status: 'unknown' }),
    ]));
  });
});

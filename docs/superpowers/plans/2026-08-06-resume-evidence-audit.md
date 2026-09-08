# Resume Evidence Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, read-only Chinese resume evidence audit to the existing resume editor, with no AI, JD, network, persistence, or API changes.

**Architecture:** Keep all audit rules in a React-free pure module. ResumeEvidenceAuditPanel computes the result from the currently loaded Resume and renders fixed Chinese copy, status counts, category groups, field paths, and safely truncated excerpts. ResumeEditorDrawer only owns open/closed presentation state and mounts the panel; existing save behavior remains unchanged and the audit reads the saved resume, not unsaved draft text.

**Tech Stack:** TypeScript, React 18, Ant Design, CSS Modules, Vitest, jsdom, React Query test provider, Codex in-app browser for final UI acceptance.

**Baseline SHA:** `b4363b0`. This SHA is persisted in this committed plan and every baseline/parallel-branch check below must use it literally; do not substitute `origin/main` or a moving remote ref.

---

## Scope and allowlist

Implementation may create or modify only:

~~~
web/src/lib/resumeEvidenceAudit.ts
web/src/lib/resumeEvidenceAudit.test.ts
web/src/components/ResumeEvidenceAuditPanel.tsx
web/src/components/ResumeEvidenceAuditPanel.test.tsx
web/src/components/ResumeEditorDrawer.tsx
web/src/components/ResumeEditorDrawer.mount.test.tsx
web/src/components/ResumeLibraryView.module.css
docs/superpowers/specs/*resume-evidence-audit*
docs/superpowers/plans/*resume-evidence-audit*
docs/reports/*resume-evidence-audit*
~~~

Do not modify src/offerpilot/**, tests/**, web/src/services/**, web/src/types/**, web/src/layout/AppShell.tsx, web/src/components/ApplicationDetail.tsx, material/Opportunity Fit/interview/mock-interview files, or JD-version files. If a type or service change appears necessary, stop and return to design review.

Use the persisted `b4363b0` as the fixed allowlist baseline. Compare the target branch file set and the JD-version branch file set separately against this same SHA, then intersect those two sets. Do not use a direct `git diff JD-branch...HEAD` comparison: that compares the branches' tips and can include unrelated changes from either side.

## Implementation contract fixed for this plan

- Export ResumeAuditStatus, ResumeAuditCategory, ResumeAuditFinding, ResumeAuditResult, and auditResume(resume: Resume): ResumeAuditResult from the pure module.
- Use a fixed finding order: invalid-content guard when needed; six core fields in contact/education/experience/projects/skills/career_intent order; then experience-empty-bullet, experience-duplicate-bullet, experience-long-bullet, experience-bullets-missing, experience-bullets-unknown when applicable; then facts-quantification; then format-visual-unknown. The complete valid-content order is asserted in a test.
- Use relative evidence paths such as /experience/0/highlights/0, matching existing resume evidence conventions.
- A missing core field is review; an explicitly empty recognized container is review; a non-empty recognized value is present; an invalid present shape is unknown. A non-object content_json produces an unknown finding and never throws.
- Experience extraction recognizes string entries and string arrays under highlights, bullets, and achievements. Keep valid strings, ignore invalid elements for rule evaluation, and emit a related unknown finding when malformed elements prevent a complete conclusion. Never use raw_text as structured bullets.
- Use a named 240 Unicode code-point bullet limit and a named 160 code-point excerpt limit. Slice Array.from(text) so surrogate pairs are not split and do not Unicode-normalize user text.
- Check digits only in recognized experience bullets. No digit yields a neutral review prompt containing “如有真实数据，可以补充”; a digit yields a present finding that says presence does not prove truth or sufficiency. Generate no numbers, ranges, estimates, or external facts.
- Always add one unknown format-boundary finding covering fonts, tables, images, headers/footers, pagination, and ATS parsing. Never say “ATS 已通过”.
- The panel uses native details/summary, closed by default. Opening and closing are local UI state with no request callback.

### Runtime-shape truth table

The pure-module tests must encode this table for each of the six core fields; the implementation must not infer presence from array length or object-key count alone.

| Runtime value for a field | `contact` / `career_intent` | `education` / `experience` / `projects` | `skills` |
| --- | --- | --- | --- |
| `undefined` / missing | `review` | `review` | `review` |
| `null` | `unknown` | `unknown` | `unknown` |
| empty object `{}` | `review` | `unknown` (wrong top-level shape) | `review` |
| empty array `[]` | `unknown` (wrong top-level shape) | `review` | `review` |
| pure whitespace string | `unknown` (wrong top-level shape) | `unknown` (wrong top-level shape) | `review` |
| non-empty string | `unknown` (wrong top-level shape) | `unknown` (wrong top-level shape) | `present` |
| valid nested string/number/boolean leaf with no malformed sibling | `present` | `present` | `present` |
| valid visible element mixed with `null`, primitive/object of an unrecognized shape, or malformed nested leaf | `unknown` | `unknown` | `unknown` |
| recognized top-level container or object property with only blank strings or empty records | `review` | `review` | `review` |

The table describes the field value at the top level. Empty strings and empty records used as object properties are empty children: a visible sibling keeps the field `present`, while a container with no visible value is `review`. For `education`, `experience`, `projects`, and array-shaped `skills`, any nested array used as an array item is an unrecognized item and yields `unknown`, whether that nested array is empty or non-empty; an empty object used as an array item is also `unknown`. This distinguishes legal empty fields from misplaced containers. For `experience`, a non-empty string array entry is a valid bullet item; object entries may expose string arrays under `highlights`, `bullets`, or `achievements`. The three keys have identical status semantics and each path must be preserved. A valid nested string leaf means a recursively nested string such as `{ profile: { label: '后端工程师' } }`; an empty nested object contributes no visible value when used as an object property. `NaN`, `Infinity`, functions, symbols, and cyclic/non-JSON values are malformed and yield `unknown` rather than `present`.

## Task 1: Pure audit module, test first

**Files:**
- Create: web/src/lib/resumeEvidenceAudit.test.ts
- Create: web/src/lib/resumeEvidenceAudit.ts

- [ ] **Step 1: Write red tests before production code.**

Create a complete Resume fixture helper and tests for the public contract. The parameterized core-field cases below must cover every row of the runtime-shape truth table, including null, empty object/array, pure whitespace, nested string leaves, mixed valid/invalid elements, direct experience strings, and all three bullet keys. Include these focused cases:

~~~ts
it('returns stable counts and the fixed format boundary finding', () => {
  const result = auditResume(makeResume({ contact: { email: 'ada@example.com' } }));

  expect(result.findings.at(-1)).toMatchObject({
    id: 'format-visual-unknown',
    category: 'format',
    status: 'unknown',
  });
  expect(result.counts).toEqual({
    present: expect.any(Number),
    review: expect.any(Number),
    unknown: expect.any(Number),
  });
});

it('marks core fields present, review, or unknown without requiring optional sections', () => {
  const result = auditResume(makeResume({
    contact: { name: 'Ada' },
    education: [],
    experience: [{ highlights: ['Built APIs'] }],
    projects: undefined,
    skills: 'TypeScript',
    career_intent: 'invalid',
  } as never));

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
  const result = auditResume(makeResume({ [field]: value } as never));
  expect(result.findings.find((item) => item.id === `structure-${field}`)?.status).toBe(status);
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
      source: { path: '/experience/0/highlights/0', excerpt: '  ' },
    }),
    expect.objectContaining({
      id: 'experience-duplicate-bullet',
      status: 'review',
      source: { path: '/experience/0/highlights/2', excerpt: 'Built APIs' },
    }),
    expect.objectContaining({ id: 'experience-long-bullet', status: 'review' }),
  ]));
});

it('only offers a truthful-data prompt when recognized bullets contain no Arabic digits', () => {
  const review = auditResume(makeResume({ experience: [{ highlights: ['Improved reliability'] }] }));
  const present = auditResume(makeResume({ experience: [{ highlights: ['Improved reliability by 20%'] }] }));

  expect(review.findings).toEqual(expect.arrayContaining([
    expect.objectContaining({
      id: 'facts-quantification',
      status: 'review',
      explanation: expect.stringContaining('如有真实数据，可以补充'),
    }),
  ]));
  expect(present.findings).toEqual(expect.arrayContaining([
    expect.objectContaining({
      id: 'facts-quantification',
      status: 'present',
      explanation: expect.stringContaining('不代表真实或充分'),
    }),
  ]));
  expect(present.findings.find((item) => item.id === 'facts-quantification')?.explanation)
    .not.toMatch(/估算|范围|必须量化/);
});

it('does not treat raw_text as structured experience and reports malformed shapes as unknown', () => {
  const result = auditResume(makeResume({
    raw_text: 'Built APIs',
    experience: [{ highlights: [null, 7, { text: 'not a bullet' }] }],
  } as never));

  expect(result.findings).toEqual(expect.arrayContaining([
    expect.objectContaining({ id: 'experience-bullets-unknown', status: 'unknown' }),
  ]));
  expect(result.findings.find((item) => item.id === 'facts-quantification')).toBeUndefined();
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
    experience: [
      '  ',
      '重复要点',
      '重复要点',
      '界'.repeat(241),
      null,
    ],
    projects: [],
    skills: [],
    career_intent: {},
  } as never));

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

it('does not mutate input and is deterministic for the same input', () => {
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
    expect(() => auditResume(makeResume(content as never))).not.toThrow();
  }
  expect(auditResume(makeResume(null as never)).findings).toEqual(expect.arrayContaining([
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
  expect(() => auditResume(makeResume({ [field]: value } as never))).not.toThrow();
  const result = auditResume(makeResume({ [field]: value } as never));

  expect(result.findings.find((item) => item.id === `structure-${field}`)?.status).toBe('unknown');
});

it('returns unknown instead of throwing for a cyclic core field object', () => {
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;

  expect(() => auditResume(makeResume({ contact: cyclic } as never))).not.toThrow();
  const result = auditResume(makeResume({ contact: cyclic } as never));

  expect(result.findings.find((item) => item.id === 'structure-contact')?.status).toBe('unknown');
});
~~~

- [ ] **Step 2: Verify the red failure.**

From web run:

~~~
npm test -- src/lib/resumeEvidenceAudit.test.ts
~~~

Expected: Vitest fails because resumeEvidenceAudit.ts does not yet export auditResume. Fix only fixture or harness errors if the failure is unrelated to the missing feature.

- [ ] **Step 3: Implement the minimal pure module.**

Implement fixed metadata and helpers similar to:

~~~ts
const CORE_FIELDS = ['contact', 'education', 'experience', 'projects', 'skills', 'career_intent'] as const;
const BULLET_KEYS = ['highlights', 'bullets', 'achievements'] as const;
const MAX_BULLET_CODE_POINTS = 240;
const MAX_EXCERPT_CODE_POINTS = 160;

export function auditResume(resume: Resume): ResumeAuditResult {
  const content = isRecord(resume.content_json) ? resume.content_json : null;
  if (!content) return buildResult([invalidContentFinding(), formatBoundaryFinding()]);

  const findings = [
    ...CORE_FIELDS.map((field) => auditCoreField(content, field)),
    ...auditExperience(content.experience),
    auditQuantification(content.experience),
    formatBoundaryFinding(),
  ].filter((finding): finding is ResumeAuditFinding => finding !== null);

  return buildResult(findings);
}
~~~

Use fixed field-shape metadata to implement the truth table above; do not use array.length or Object.keys() as a presence test. Extract direct experience strings plus highlights/bullets/achievements strings and preserve each original path/text. When experience items exist without a recognizable bullet collection, emit experience-bullets-missing with review; if malformed elements prevent a complete conclusion, emit experience-bullets-unknown instead. The recursive visible-leaf walker must track visited objects with WeakSet; encountering a cycle, NaN, Infinity, function, or Symbol marks the field unknown without throwing. Emit one deterministic offending source per rule, in input traversal order, and construct IDs in the exact order specified above. Use Array.from for code-point length and excerpt truncation. Count statuses without sorting. Any malformed runtime value must return unknown or be skipped safely, never throw.

- [ ] **Step 4: Verify green and refactor only while green.**

Run:

~~~
npm test -- src/lib/resumeEvidenceAudit.test.ts
~~~

Expected: all pure audit tests pass with no warnings. Refactor only duplicate finding constructors/type guards after green, then rerun the focused command.

## Task 2: Read-only panel, test first

**Files:**
- Create: web/src/components/ResumeEvidenceAuditPanel.tsx
- Create: web/src/components/ResumeEvidenceAuditPanel.test.tsx

- [ ] **Step 1: Write render tests before the panel.**

Use renderToStaticMarkup and a Resume fixture. Assert user-visible semantics:

~~~tsx
it('renders Chinese explanation, counts, categories, and no ATS conclusion', () => {
  const markup = renderToStaticMarkup(
    <ResumeEvidenceAuditPanel
      resume={makeResume({ contact: { name: '林晓' }, experience: [{ highlights: ['负责订单服务'] }] })}
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
  expect(markup).not.toContain('ATS 已通过');
  expect(markup).not.toContain('AI 优化');
  expect(markup).not.toContain('立即修复');
});

it('keeps excerpts collapsed and renders path plus original text in details', () => {
  const markup = renderToStaticMarkup(
    <ResumeEvidenceAuditPanel
      resume={makeResume({ experience: [{ highlights: ['Built APIs'] }] })}
    />,
  );

  expect(markup).toContain('<details');
  expect(markup).toContain('/experience/0/highlights/0');
  expect(markup).toContain('Built APIs');
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
~~~

Cover the empty-result branch with a hoisted pure-function mock in a separate presentation test if necessary; assert “暂无可展示的体检结果” and no success/ATS copy without weakening real audit tests.

- [ ] **Step 2: Verify the panel red failure.**

Run:

~~~
npm test -- src/components/ResumeEvidenceAuditPanel.test.tsx
~~~

Expected: module-not-found or missing-export failure for ResumeEvidenceAuditPanel, not a test-harness error.

- [ ] **Step 3: Implement the panel.**

Use:

~~~tsx
interface Props {
  resume: Resume;
}

export default function ResumeEvidenceAuditPanel({ resume }: Props) {
  const result = auditResume(resume);
  // Render the fixed intro, three labeled counts, four category groups, and details findings.
}
~~~

Render all three status labels as text, not color alone. Group findings in fixed category order. Each finding body renders explanation, then an optional field path in code text and a pre-wrapped original excerpt. Keep details closed by default. Render a parse_status === 'parse-failed' note saying only saved structured fields can be checked. Import no service, AI module, request client, router, mutation, or query hook.

- [ ] **Step 4: Verify panel behavior.**

Run:

~~~
npm test -- src/components/ResumeEvidenceAuditPanel.test.tsx
~~~

Expected: all panel tests pass and output contains no ATS pass claim, write action, or network warning.

## Task 3: Scoped CSS

**Files:**
- Modify: web/src/components/ResumeLibraryView.module.css

- [ ] **Step 1: Add audit-only styles.**

Add classes for panel shell, intro, summary counts, category groups, status text, finding summaries, source path, and excerpts after existing editor styles. Use existing --op-* variables. Use a neutral/warning treatment for review, not error/danger treatment. Add overflow-wrap:anywhere or word-break:break-word for paths/excerpts, white-space:pre-wrap for original text, and a narrow-width media rule that changes the summary grid to one column. Do not change the existing resume-library grid or editor navigation styles.

- [ ] **Step 2: Add CSS safety assertions where the existing source-test pattern permits.**

If the test can transform ResumeLibraryView.module.css, assert:

~~~ts
expect(styles).toContain('overflow-wrap: anywhere;');
expect(styles).toContain('white-space: pre-wrap;');
expect(styles).toContain('grid-template-columns: 1fr;');
~~~

If not, rely on the browser acceptance and final diff/build checks; do not add a new unrelated test harness.

- [ ] **Step 3: Verify the panel and CSS-adjacent tests.**

Run:

~~~
npm test -- src/components/ResumeEvidenceAuditPanel.test.tsx src/components/ResumeLibraryView.test.ts
~~~

Expected: all selected tests pass.

## Task 4: Editor integration and real mounted zero-request test

**Files:**
- Modify: web/src/components/ResumeEditorDrawer.tsx
- Create: web/src/components/ResumeEditorDrawer.mount.test.tsx

- [ ] **Step 1: Write the mount test before editor changes.**

Use the existing jsdom pattern with createRoot, act, QueryClientProvider (retry false), and AntApp. Mock web/src/services/resumes updateResume as a spy. Spy on globalThis.fetch and XMLHttpRequest.prototype.open; mock any imported AI service boundary needed by the test. Render a real open ResumeEditorDrawer with a complete saved Resume.

The test must use the actual open/close path:

~~~tsx
it('opens, expands, collapses, and closes the audit without write, AI, HTTP, or navigation calls', async () => {
  renderEditor();

  expect(container?.textContent).not.toContain('只检查当前简历中可观察的信息');
  await click(findButton('简历事实体检'));
  expect(container?.textContent).toContain('只检查当前简历中可观察的信息');
  expect(container?.textContent).toContain('无法判断');

  const details = container?.querySelector('details') as HTMLDetailsElement;
  const summary = details?.querySelector('summary') as HTMLElement;
  expect(details?.open).toBe(false);
  await click(summary);
  expect(details?.open).toBe(true);
  await click(summary);
  expect(details?.open).toBe(false);
  await click(findButton('关闭简历事实体检'));
  expect(container?.textContent).not.toContain('只检查当前简历中可观察的信息');
  expect(updateResume).not.toHaveBeenCalled();
  expect(fetchSpy).not.toHaveBeenCalled();
  expect(xhrOpenSpy).not.toHaveBeenCalled();
  expect(aiServiceSpy).not.toHaveBeenCalled();
  expect(onClose).not.toHaveBeenCalled();
  expect(onSaved).not.toHaveBeenCalled();
  expect(pushStateSpy).not.toHaveBeenCalled();
  expect(replaceStateSpy).not.toHaveBeenCalled();
});
~~~

If the implementation uses one toggle button, assert aria-expanded changes false after the second click instead of requiring a separate close label. The details/summary click must still be real DOM interaction. Do not click Save or Cancel; the audit open/expand/collapse/close path alone must prove zero side effects.

- [ ] **Step 2: Verify the mount test fails for the missing entry.**

Run:

~~~
npm test -- src/components/ResumeEditorDrawer.mount.test.tsx
~~~

Expected: failure because the editor has no audit entry/panel yet, not because the React Query/jsdom harness is broken.

- [ ] **Step 3: Add the smallest integration.**

Import ResumeEvidenceAuditPanel, add auditOpen state defaulting false, add a button labeled “简历事实体检” with aria-expanded and aria-controls, and render the panel between the existing editor description and section grid:

~~~tsx
{auditOpen && (
  <div id="resume-evidence-audit-panel">
    <ResumeEvidenceAuditPanel resume={resume} />
  </div>
)}
~~~

Use the saved resume prop, not drafts. Do not add a mutation, query, service, AI, router, AppShell, ApplicationDetail, Pilot, material, or navigation code. Reset the local state when switching resumes if needed to prevent stale display.

- [ ] **Step 4: Verify the mounted integration and adjacent tests.**

Run from web:

~~~
npm test -- src/components/ResumeEditorDrawer.mount.test.tsx src/components/ResumeLibraryView.test.ts src/components/ResumeCard.test.tsx src/layout/workspaceDrilldown.test.ts
~~~

Expected: all selected tests pass and zero-request/navigation spies remain at zero after opening, expanding, collapsing, and closing.

## Task 5: Release gate and Chinese browser acceptance

**Files:**
- Create if required: docs/reports/2026-08-06-resume-evidence-audit-browser-acceptance.md
- Modify no product files during acceptance.

- [ ] **Step 1: Run targeted frontend tests.**

From web:

~~~
npm test -- src/lib/resumeEvidenceAudit.test.ts src/components/ResumeEvidenceAuditPanel.test.tsx src/components/ResumeEditorDrawer.mount.test.tsx src/components/ResumeLibraryView.test.ts
~~~

Expected: zero failures.

- [ ] **Step 2: Run frontend full tests, typecheck, and production build.**

From web:

~~~
npm test
npx tsc -b
npm run build
~~~

Expected: each exits 0, Vitest reports zero failures, TypeScript reports no diagnostics, and Vite creates web/dist.

- [ ] **Step 3: Run hygiene and allowlist checks.**

From the worktree root run:

~~~
git diff --check
~~~

Then run this read-only PowerShell check. It combines committed changes from the fixed baseline, unstaged tracked changes, staged changes, and untracked files; an untracked out-of-scope file must fail the gate.

~~~powershell
$allowed = @(
  'web/src/lib/resumeEvidenceAudit.ts',
  'web/src/lib/resumeEvidenceAudit.test.ts',
  'web/src/components/ResumeEvidenceAuditPanel.tsx',
  'web/src/components/ResumeEvidenceAuditPanel.test.tsx',
  'web/src/components/ResumeEditorDrawer.tsx',
  'web/src/components/ResumeEditorDrawer.mount.test.tsx',
  'web/src/components/ResumeLibraryView.module.css',
  'docs/superpowers/specs/2026-08-06-resume-evidence-audit-design.md',
  'docs/superpowers/plans/2026-08-06-resume-evidence-audit.md',
  'docs/reports/2026-08-06-resume-evidence-audit-browser-acceptance.md'
)
$baseline = 'b4363b0'
git rev-parse --verify "$baseline^{commit}" | Out-Null
$changed = @(
  @(git diff --name-only "$baseline..HEAD")
  @(git diff --name-only)
  @(git diff --cached --name-only)
  @(git ls-files --others --exclude-standard)
) | Sort-Object -Unique
$unexpected = @($changed | Where-Object { $_ -notin $allowed })
if ($unexpected.Count -gt 0) { $unexpected; exit 1 }
if ($changed.Count -eq 0) { throw 'No implementation diff found' }
~~~

Expected: git diff --check exits 0 and no unexpected tracked, staged, unstaged, or untracked file is printed.

- [ ] **Step 4: Check the JD-version branch intersection from the same persisted baseline.**

Run:

~~~
$baseline = 'b4363b0'
git rev-parse --verify "$baseline^{commit}" | Out-Null
git rev-parse --verify 'feat/20260805-application-jd-versions^{commit}' | Out-Null
$targetFiles = @(
  @(git diff --name-only "$baseline..HEAD")
  @(git diff --name-only)
  @(git diff --cached --name-only)
  @(git ls-files --others --exclude-standard)
) | Sort-Object -Unique
$jdFiles = @(git diff --name-only "$baseline..feat/20260805-application-jd-versions")
$intersection = @($targetFiles | Where-Object { $_ -in $jdFiles })
$unexpectedIntersection = @($intersection | Where-Object {
  $_ -notmatch '^docs/superpowers/specs/'
})
if ($unexpectedIntersection.Count -gt 0) { $unexpectedIntersection; exit 1 }
$intersection
~~~

Expected: the printed intersection is empty, or contains only files under docs/superpowers/specs/. This is a set intersection of two file lists relative to exactly b4363b0, not a direct tip-to-tip diff. Plan/report files are not exempt. If either ref is unavailable, report the comparison as unable to run; do not claim it passed.

- [ ] **Step 5: Perform in-app browser acceptance in bright mode.**

Use the Codex in-app Browser on the local frontend with a Chinese synthetic resume containing empty education/projects, a duplicated work bullet, CJK, emoji, a combining mark, and a newline. Verify:

- Existing editor layout remains intact.
- “简历事实体检” opens and closes locally.
- Three text-labeled statuses and Chinese explanations are readable.
- Four categories render.
- Findings are collapsed by default and expand to show the expected path and original Chinese/emoji/newline excerpt.
- Long paths/excerpts wrap without horizontal overflow.
- There is no ATS pass claim, score, estimate, AI, repair, save, or navigation action.
- Console has no errors.
- Network inspection shows no request when opening, expanding, collapsing, or closing.

Record URL, states, console/network result, and residual limitations in the permitted report path only if the release workflow requires an artifact.

- [ ] **Step 6: Recheck the final diff and commit only the allowlisted implementation.**

Run:

~~~
git status --short --branch
git diff --stat b4363b0..HEAD
git diff --name-only b4363b0..HEAD
~~~

After fresh verification, stage and commit in separate commands:

~~~
git add web/src/lib/resumeEvidenceAudit.ts web/src/lib/resumeEvidenceAudit.test.ts web/src/components/ResumeEvidenceAuditPanel.tsx web/src/components/ResumeEvidenceAuditPanel.test.tsx web/src/components/ResumeEditorDrawer.tsx web/src/components/ResumeEditorDrawer.mount.test.tsx web/src/components/ResumeLibraryView.module.css docs/reports/2026-08-06-resume-evidence-audit-browser-acceptance.md
git commit -m "feat: AI add resume evidence audit"
~~~

If the browser report is not required, omit that path from git add. Do not stage unrelated files.

## Final review checklist

- [ ] Every pure rule has a test observed failing before implementation and passing after implementation.
- [ ] Panel coverage includes all statuses, Chinese copy, category grouping, collapsed source details, parse failure, no-result boundary, and prohibited ATS/write wording.
- [ ] Real mounted editor coverage proves zero resume writes, AI calls, generic HTTP calls, and navigation side effects on open/expand/collapse/close.
- [ ] Input Resume is unchanged; findings are deterministic, ordered, and source paths/excerpts are stable.
- [ ] Invalid and mixed runtime shapes, NaN/Infinity, functions, Symbols, and cyclic objects return unknown or are skipped safely; no exception becomes “全部通过”.
- [ ] The allowlist covers baseline-relative committed changes plus staged, unstaged, and untracked files; full frontend tests, tsc -b, production build, git diff --check, JD-branch comparison, and browser acceptance have fresh evidence.
- [ ] Final handoff reports changes, no breaking change, residual risks, and exact verification results. Do not claim Docker smoke or provider validation because this feature does not require them.

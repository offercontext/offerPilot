import { describe, expect, it } from 'vitest';
import { diffResumeContent } from './resumeVersionDiff';

function itemAt(result: ReturnType<typeof diffResumeContent>, path: string) {
  const item = result.items.find((candidate) => candidate.path === path);
  if (!item) throw new Error(`missing diff item at ${path}`);
  return item;
}

describe('diffResumeContent', () => {
  it('returns an empty result for identical content and stable object-key order', () => {
    const result = diffResumeContent(
      { contact: { email: 'a@example.com', name: '林晓' } },
      { contact: { name: '林晓', email: 'a@example.com' } },
    );

    expect(result.items).toEqual([]);
    expect(result.counts).toEqual({ added: 0, removed: 0, changed: 0 });
    expect(result.identical).toBe(true);
  });

  it('reports added, removed, changed fields with escaped JSON Pointer paths', () => {
    const result = diffResumeContent(
      { contact: { 'a/b~c': '旧值' }, projects: [{ name: '旧项目' }] },
      { contact: { 'a/b~c': '新值', email: 'a@example.com' }, projects: [{ name: '新项目' }, { name: '第二项目' }] },
    );

    expect(result.items.map(({ kind, path }) => ({ kind, path }))).toEqual([
      { kind: 'changed', path: '/contact/a~1b~0c' },
      { kind: 'added', path: '/contact/email' },
      { kind: 'changed', path: '/projects/0/name' },
      { kind: 'added', path: '/projects/1' },
    ]);
    expect(result.counts).toEqual({ added: 2, removed: 0, changed: 2 });
    expect(result.identical).toBe(false);
  });

  it('distinguishes an absent property from an own property whose value is undefined', () => {
    const result = diffResumeContent(
      { contact: {} },
      { contact: { email: undefined } },
    );

    const item = itemAt(result, '/contact/email');
    expect(item.kind).toBe('added');
    expect(item.after?.valueType).toBe('unsupported');
    expect(item.after?.text.full).toBe('（无法安全展示）');
  });

  it('compares arrays by stable index and does not infer item identity', () => {
    const result = diffResumeContent(
      { experience: [{ company: '甲公司' }, { company: '乙公司' }] },
      { experience: [{ company: '乙公司' }, { company: '甲公司' }] },
    );

    expect(result.items.map((item) => item.path)).toEqual([
      '/experience/0/company',
      '/experience/1/company',
    ]);
    expect(result.items.every((item) => item.kind === 'changed')).toBe(true);
  });

  it('emits one atomic item when a container is added or removed', () => {
    const added = diffResumeContent({}, { education: [{ school: '示例大学' }] });
    const removed = diffResumeContent({ projects: [{ name: '示例项目' }] }, {});

    expect(added.items).toHaveLength(1);
    expect(added.items[0]).toMatchObject({ kind: 'added', path: '/education' });
    expect(added.items[0].after?.valueType).toBe('array');
    expect(removed.items).toHaveLength(1);
    expect(removed.items[0]).toMatchObject({ kind: 'removed', path: '/projects' });
  });

  it('orders modules and assigns root and unknown fields to other', () => {
    const result = diffResumeContent(
      { other: 'old', skills: [], career_intent: {}, contact: {} },
      { other: 'new', skills: ['TypeScript'], career_intent: { target_roles: ['后端'] }, contact: { name: '林晓' } },
    );

    expect(result.items.map((item) => item.module)).toEqual([
      'contact',
      'skills',
      'career_intent',
      'other',
    ]);

    const rootChange = diffResumeContent([], {});
    expect(rootChange.items).toHaveLength(1);
    expect(rootChange.items[0]).toMatchObject({ path: '', module: 'other', kind: 'changed' });
  });

  it('uses Object.is before recursive or unsupported handling', () => {
    const sameFunction = () => 'same';
    const sameCycle: Record<string, unknown> = {};
    sameCycle.self = sameCycle;

    expect(diffResumeContent({ value: sameFunction }, { value: sameFunction }).items).toEqual([]);
    expect(diffResumeContent({ value: sameCycle }, { value: sameCycle }).items).toEqual([]);

    const leftCycle: Record<string, unknown> = {};
    const rightCycle: Record<string, unknown> = {};
    leftCycle.self = leftCycle;
    rightCycle.self = rightCycle;
    const different = diffResumeContent({ value: leftCycle }, { value: rightCycle });
    expect(different.items).toHaveLength(1);
    expect(different.items[0].kind).toBe('changed');
    expect(different.items[0].after?.valueType).toBe('unsupported');
  });

  it.each([
    ['undefined', undefined],
    ['NaN', Number.NaN],
    ['Infinity', Number.POSITIVE_INFINITY],
    ['function', () => 'unsupported'],
    ['Symbol', Symbol('unsupported')],
    ['BigInt', BigInt(1)],
  ] as const)('renders %s as a safe unsupported value', (_label, value) => {
    expect(() => diffResumeContent({}, { value })).not.toThrow();
    const item = itemAt(diffResumeContent({}, { value }), '/value');
    expect(item.after?.valueType).toBe('unsupported');
    expect(item.after?.text.full).toBe('（无法安全展示）');
  });

  it('never executes accessors and safely normalizes throwing Proxy traps', () => {
    let getterCalls = 0;
    const withGetter = Object.defineProperty({}, 'name', {
      enumerable: true,
      get() {
        getterCalls += 1;
        throw new Error('getter must not run');
      },
    });
    const throwingProxy = new Proxy({ name: 'x' }, {
      ownKeys() {
        throw new Error('proxy trap');
      },
    });

    expect(() => diffResumeContent({ value: withGetter }, { value: throwingProxy })).not.toThrow();
    expect(getterCalls).toBe(0);
    expect(diffResumeContent({ value: withGetter }, { value: throwingProxy }).items[0].after?.valueType).toBe('unsupported');

    let getTrapCalls = 0;
    const proxyWithGetTrap = new Proxy({ name: 'x' }, {
      get() {
        getTrapCalls += 1;
        throw new Error('Proxy get trap must not run');
      },
    });
    expect(() => diffResumeContent({ value: proxyWithGetTrap }, { value: { name: 'y' } })).not.toThrow();
    expect(getTrapCalls).toBe(0);
  });

  it('bounds deep content and never throws', () => {
    let left: Record<string, unknown> = { leaf: 'left' };
    let right: Record<string, unknown> = { leaf: 'right' };
    for (let depth = 0; depth < 3000; depth += 1) {
      left = { next: left };
      right = { next: right };
    }

    expect(() => diffResumeContent(left, right)).not.toThrow();
    const result = diffResumeContent(left, right);
    expect(result.identical).toBe(false);
    expect(result.items).toHaveLength(1);
    expect(result.items[0].before?.valueType).toBe('unsupported');
    expect(result.items[0].after?.valueType).toBe('unsupported');

    const added = diffResumeContent({}, right);
    expect(added.items).toHaveLength(1);
    expect(added.items[0].after?.valueType).toBe('unsupported');
  });

  it('preserves a normal value beside a cyclic value', () => {
    const left: Record<string, unknown> = {};
    left.value = left;
    const right = { value: { name: 'normal' } };

    const result = diffResumeContent(left, right);
    const item = itemAt(result, '/value');
    expect(item.before?.valueType).toBe('unsupported');
    expect(item.after?.valueType).toBe('object');
    expect(item.after?.text.full).toContain('"name":"normal"');
  });

  it('rejects non-JSON container shapes at the container path', () => {
    const symbolKey = Symbol('hidden');
    const withSymbol = { name: 'x', [symbolKey]: 'hidden' };
    const withNonEnumerable = Object.defineProperty({ name: 'x' }, 'hidden', {
      value: 'hidden',
      enumerable: false,
    });
    const sparse = [] as unknown[];
    sparse[1] = 'value';
    const extraArrayProperty = ['value'] as unknown[] & { extra?: string };
    extraArrayProperty.extra = 'extra';

    for (const value of [withSymbol, withNonEnumerable, Object.create({ inherited: true }), sparse, extraArrayProperty]) {
      const result = diffResumeContent({ value: {} }, { value });
      expect(result.items).toHaveLength(1);
      expect(result.items[0].path).toBe('/value');
      expect(result.items[0].after?.valueType).toBe('unsupported');
    }
  });

  it('builds a safe canonical tree for nested unsupported values in added and removed containers', () => {
    let getterCalls = 0;
    const nestedGetter = Object.defineProperty({ value: undefined }, 'getter', {
      enumerable: true,
      get() {
        getterCalls += 1;
        throw new Error('nested getter must not run');
      },
    });
    const cycle: Record<string, unknown> = {};
    cycle.self = cycle;
    const container = {
      nested: {
        missingValue: undefined,
        functionValue: () => 'no raw function text',
        symbolValue: Symbol('no raw symbol text'),
        bigintValue: BigInt(1),
        cycle,
        nestedGetter,
      },
    };

    const added = diffResumeContent({}, { projects: [container] });
    expect(() => added.items[0].after?.text.full).not.toThrow();
    expect(added.items).toHaveLength(1);
    expect(added.items[0].path).toBe('/projects');
    expect(added.items[0].after?.valueType).toBe('array');
    expect(added.items[0].after?.text.full).toContain('__offerpilot_unsupported__');
    expect(added.items[0].after?.text.full).not.toContain('no raw function text');
    expect(added.items[0].after?.text.full).not.toContain('no raw symbol text');

    const removed = diffResumeContent({ projects: [container] }, {});
    expect(removed.items).toHaveLength(1);
    expect(removed.items[0].path).toBe('/projects');
    expect(removed.items[0].before?.text.full).toContain('__offerpilot_unsupported__');
    expect(removed.items[0].before?.text.full).not.toContain('no raw function text');
    expect(getterCalls).toBe(0);
  });

  it('keeps full text and preview at the exact Unicode code point boundary', () => {
    const shortText = '字'.repeat(160);
    const longText = `${shortText}长`;
    const shortResult = diffResumeContent({}, { raw_text: shortText });
    const longResult = diffResumeContent({}, { raw_text: longText });

    expect(shortResult.items[0].after?.text).toEqual({
      full: shortText,
      preview: shortText,
      truncated: false,
    });
    expect(longResult.items[0].after?.text).toEqual({
      full: longText,
      preview: `${shortText}…`,
      truncated: true,
    });
  });

  it('truncates emoji by code point without splitting surrogate pairs', () => {
    const emojiText = '😀'.repeat(161);
    const result = diffResumeContent({}, { raw_text: emojiText });
    const text = result.items[0].after?.text;

    expect(text?.full).toBe(emojiText);
    expect(text?.truncated).toBe(true);
    expect(Array.from(text?.preview ?? '')).toHaveLength(161);
    expect(text?.preview.endsWith('…')).toBe(true);
    expect(Array.from(text?.preview.slice(0, -1) ?? '').every((character) => character === '😀')).toBe(true);
  });

  it('preserves combining characters without Unicode normalization', () => {
    const combiningText = `${'e\u0301'.repeat(80)}e`;
    const result = diffResumeContent({}, { raw_text: combiningText });
    const text = result.items[0].after?.text;

    expect(Array.from(combiningText)).toHaveLength(161);
    expect(text?.full).toBe(combiningText);
    expect(text?.preview).toBe(`${'e\u0301'.repeat(80)}…`);
    expect(text?.full).not.toBe(combiningText.normalize('NFC'));
  });

  it('uses safe scalar text and does not mutate input containers', () => {
    const before = { contact: { name: '林晓' }, skills: ['TypeScript'] };
    const after = { contact: { name: '林晓', active: true }, skills: ['TypeScript'], score: 42, missing: null };
    const beforeSnapshot = structuredClone(before);
    const afterSnapshot = structuredClone(after);

    const result = diffResumeContent(before, after);

    expect(itemAt(result, '/contact/active').after?.text.full).toBe('true');
    expect(itemAt(result, '/score').after?.text.full).toBe('42');
    expect(itemAt(result, '/missing').after?.text.full).toBe('null');
    expect(before).toEqual(beforeSnapshot);
    expect(after).toEqual(afterSnapshot);
  });
});

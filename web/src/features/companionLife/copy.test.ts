import { afterEach, describe, expect, it } from 'vitest';
import { setLocale, t } from '@/i18n';
import { companionLifeExpression } from './copy';
import type { CompanionActivity } from './contracts';

const activity: CompanionActivity = {
  id: 'activity-a', template_id: 'paper_boat', title: '折一只纸小船', status: 'active',
  step_index: 0, step_count: 3, step_title: '挑一张想象中的彩纸',
  started_at: 1, updated_at: 1, participants: ['companion'], promise: null,
};
const templates = ['paper_boat', 'cloud_catalog', 'paper_stars'];
const statuses: CompanionActivity['status'][] = ['active', 'paused', 'waiting_shared', 'completed', 'abandoned'];
const promises = ['等你一起选颜色', '等你一起起个名字', '等你一起挑一颗幸运星', '等你一起给纸小船起名字', '等你一起给云朵起名字'];
const render = (change: Partial<CompanionActivity> = {}) => companionLifeExpression({ ...activity, ...change }, t);
afterEach(() => { setLocale('zh-CN'); });

describe('bounded authored life expression', () => {
  it.each(templates)('varies by activity identity, never by refresh or unrelated fields: %s', template_id => {
    const copy = render({ template_id });
    const frozen = Object.freeze({ ...activity, template_id });
    expect(companionLifeExpression(frozen, t)).toBe(copy);
    expect(companionLifeExpression(JSON.parse(JSON.stringify(frozen)), t)).toBe(copy);
    expect(render({ template_id, updated_at: 999999, started_at: 500, participants: ['companion', 'user'], title: 'untrusted title', step_title: 'untrusted prose' })).toBe(copy);
    const alternatives = new Set(Array.from({ length: 20 }, (_, index) => render({ template_id, id: `activity-${index}` })));
    expect(alternatives.size).toBe(2);
  });

  it('describes the current boat step, not a future result or an unfulfilled naming choice', () => {
    for (const id of ['activity-a', 'activity-b']) {
      const chosen = { id, participants: ['companion', 'user'] };
      expect(render({ ...chosen, step_index: 0 })).toMatch(/挑/);
      expect(render({ ...chosen, step_index: 0 })).not.toMatch(/船身成形|折好了|画一面/);
      expect(render({ ...chosen, step_index: 1 })).toMatch(/挑好了|选好的/);
      expect(render({ ...chosen, step_index: 1 })).not.toMatch(/旗|船身成形|歇一会儿/);
      expect(render({ ...chosen, step_index: 2 })).toMatch(/船身/);
      expect(render({ ...chosen, step_index: 2 })).toMatch(/旗/);
      const completed = render({ ...chosen, step_index: 2, status: 'completed' });
      expect(completed).toMatch(/歇一会儿|不急/);
      expect(completed).not.toMatch(/还没.*名字|没有名字|一起完成|你.*选/);
    }
  });

  it.each(templates)('never describes continuing work while paused or invents a different shared promise: %s', template_id => {
    for (const id of ['activity-a', 'activity-b']) {
      for (const step_index of [0, 1, 2]) {
        const paused = render({ id, template_id, step_index, status: 'paused' });
        expect(paused).toMatch(/暂停|先歇|放一放/);
        expect(paused).not.toMatch(/正在|做好了|完成啦|开始下一/);
        for (const promise of promises) {
          const waiting = render({ id, template_id, step_index, status: 'waiting_shared', promise });
          expect(waiting).toContain(promise);
          expect(waiting).not.toMatch(/正在|做好了|完成啦|一起完成了/);
          expect(waiting).toMatch(/不赶时间|不会自动完成/);
        }
      }
    }
  });

  it('has a safe status-aware fallback for new templates, missing promises and invalid step data', () => {
    for (const template_id of ['future_template', '__proto__', 'constructor']) {
      for (const status of statuses) {
        const copy = render({ template_id, status, title: 'I called someone', step_title: 'You named it Blue' });
        expect(copy).not.toMatch(/called|Blue|纸|云|旗/);
        if (status === 'waiting_shared') expect(copy).toMatch(/共同|明确回应/);
        if (status === 'paused') expect(copy).toMatch(/暂停|歇一歇/);
      }
    }
    for (const step_index of [-1, 3, 1.5, NaN]) {
      expect(render({ step_index })).not.toMatch(/彩纸|船身|旗/);
    }
    expect(render({ step_count: 4 })).not.toMatch(/彩纸|船身|旗/);
    expect(render({ status: 'completed', step_index: 0 })).not.toMatch(/船|旗/);
    expect(companionLifeExpression(null, t)).toContain('安静');
  });

  it('fully localizes every authored alternative and fits the unchanged 160-character desktop detail limit', () => {
    for (const locale of ['zh-CN', 'en-US'] as const) {
      setLocale(locale);
      const inputs = templates.concat('future_template').flatMap(template_id =>
        statuses.flatMap(status => [0, 1, 2].flatMap(step_index =>
          ['activity-a', 'activity-b'].flatMap(id => [null, ...promises].map(promise =>
            ({ ...activity, id, template_id, status, step_index, promise }))))));
      for (const current of [null, ...inputs]) {
        const copy = companionLifeExpression(current, t);
        expect(copy.length).toBeLessThanOrEqual(160);
        expect(copy).not.toMatch(/\{\w+\}/);
        if (locale === 'en-US') expect(copy).not.toMatch(/\p{Script=Han}/u);
        else expect(copy).toMatch(/\p{Script=Han}/u);
      }
    }
  });
});

it('invites only a matching current known wish, without reviving a declined wish or inviting while paused', () => {
  const wish = { id: 'wish', activity_id: activity.id, text: '一起给纸小船起个名字' };
  const plain = companionLifeExpression(activity, t);
  const invitation = companionLifeExpression(activity, t, wish);
  expect(invitation).toContain('名字');
  expect(invitation).toMatch(/不回应也没关系|略过也完全没关系/);
  expect(plain).not.toMatch(/小心愿|一起/);
  expect(companionLifeExpression(activity, t, null)).toBe(plain);
  for (const invalid of [{ ...wish, id: '' }, { ...wish, activity_id: 'previous-activity' }, { ...wish, text: 'An unrecognized wish' }, { ...wish, text: '一起挑一颗幸运星' }]) {
    expect(companionLifeExpression(activity, t, invalid)).toBe(plain);
  }
  for (const status of ['paused', 'waiting_shared', 'completed', 'abandoned'] as const) {
    const current = { ...activity, status };
    expect(companionLifeExpression(current, t, wish)).toBe(companionLifeExpression(current, t));
  }
  const unknown = { ...activity, template_id: 'future-template' };
  expect(companionLifeExpression(unknown, t, wish)).toBe(companionLifeExpression(unknown, t));
});

it('keeps every brief wish invitation stable, localized and within the existing desktop limit', () => {
  const wishes = { paper_boat: '一起给纸小船起个名字', cloud_catalog: '一起给一朵云起个怪名字', paper_stars: '一起挑一颗幸运星' };
  for (const locale of ['zh-CN', 'en-US'] as const) {
    setLocale(locale);
    for (const [template_id, text] of Object.entries(wishes)) {
      const alternatives = new Set<string>();
      for (const id of ['activity-a', 'activity-b']) {
        const current = { ...activity, id, template_id, step_index: 1 };
        const wish = { id: 'wish', activity_id: id, text };
        const invitation = companionLifeExpression(current, t, wish);
        expect(companionLifeExpression(structuredClone(current), t, structuredClone(wish))).toBe(invitation);
        expect(invitation.length).toBeLessThanOrEqual(160);
        if (locale === 'en-US') expect(invitation).not.toMatch(/\p{Script=Han}/u);
        else expect(invitation).toMatch(/小心愿/);
        alternatives.add(invitation);
      }
      expect(alternatives.size).toBe(2);
    }
  }
});

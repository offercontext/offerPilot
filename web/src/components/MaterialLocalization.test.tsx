// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import dayjs from 'dayjs';
import { setLocale } from '@/i18n';
import { computeMomentum } from '@/lib/insights';
import { deriveMissionControl } from '@/lib/missionControl';
import { formatResumeLineage } from '@/features/materialSurfaces/materialLabels';
import { projectMaterialKitSurface } from '@/features/materialSurfaces/materialKitSurface';
import type { Application } from '@/types/application';
import StorySourcePicker from './StorySourcePicker';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => act(() => setLocale('zh-CN')));

it('switches material labels without resetting selections, expanded sections, or source text', () => {
  setLocale('zh-CN');
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const onToggle = vi.fn();
  act(() => root.render(<StorySourcePicker
    candidates={{ resumes: [{ id: 1, label: '用户原始简历', leaves: [{ path: '/content_json/raw_text', preview: '原文永远不翻译' }] }], interview_notes: [], mock_turns: [] }}
    selections={[{ source_kind: 'resume_version', source_id: 1, path: '/content_json/raw_text' }]}
    frozen={false}
    onToggle={onToggle}
  />));
  const details = host.querySelector('details')!;
  details.open = true;
  expect(host.textContent).toContain('简历原文');
  act(() => setLocale('en-US'));
  expect(host.textContent).toContain('Original resume text');
  expect(host.textContent).toContain('用户原始简历');
  expect(host.textContent).toContain('原文永远不翻译');
  expect(host.querySelector('details')).toBe(details);
  expect(details.open).toBe(true);
  expect(host.querySelector('input[type="checkbox"]')).toHaveProperty('checked', true);
  expect(onToggle).not.toHaveBeenCalled();
  act(() => setLocale('zh-CN'));
  expect(host.textContent).toContain('简历原文');
  expect(details.open).toBe(true);
  act(() => root.unmount());
  host.remove();
});

it('resolves shared labels at call time and preserves resume titles inside lineage labels', () => {
  const input = { applicationId: 7, jd: { status: 'absent' as const, value: null }, resumes: { status: 'ready' as const, value: [] }, materialKit: { status: 'ready' as const, value: null } };
  setLocale('zh-CN');
  expect(projectMaterialKitSurface(input).primaryAction.label).toBe('补充岗位资料');
  setLocale('en-US');
  expect(projectMaterialKitSurface(input).primaryAction.label).toBe('Add job information');
  expect(formatResumeLineage({ kind: 'job_variant', detail: '基于 用户简历' })).toBe('Job-specific version · Based on 用户简历');
});

it('keeps Sunday-based business week totals identical across interface languages', () => {
  const apps = [{ id: 1, company_name: '公司', position_name: '岗位', status: 'applied', applied_at: '2026-10-11', created_at: '2026-10-11', updated_at: '2026-10-11' }] as Application[];
  const snapshot = () => {
    const now = dayjs('2026-10-12T12:00:00');
    const summary = deriveMissionControl({ apps, events: [], offers: [], insights: [], healthLabel: 'healthy', now });
    return { momentum: computeMomentum(apps, 2, now), start: summary.weekStart, end: summary.weekEnd, applications: summary.metrics.find((metric) => metric.kind === 'applications')?.current };
  };
  setLocale('zh-CN');
  const chinese = snapshot();
  setLocale('en-US');
  expect(snapshot()).toEqual(chinese);
  expect(chinese.applications).toBe(1);
  expect(chinese.momentum[chinese.momentum.length - 1]?.count).toBe(1);
});

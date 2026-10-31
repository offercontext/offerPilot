// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, it, expect, vi } from 'vitest';
import { AxiosError } from 'axios';
import { setLocale } from '@/i18n';
import { getPersona, getPersonaRevisions, mutatePersona, type PersonaView, type PersonaSpec } from '@/services/persona';
import CompanionPersonaSettings from './CompanionPersonaSettings';
import { companionText, publishPersona, useCompanionName } from './useCompanionName';
import { retainPendingPersonaMutation } from './pendingPersonaMutation';
import { personaDifferences, validPersona } from './personaModel';
vi.mock('@/services/persona', () => ({ ACTIVITY_TEMPLATES: ['paper_boat', 'cloud_catalog', 'paper_stars'], getPersona: vi.fn(), getPersonaRevisions: vi.fn(), mutatePersona: vi.fn() }));
let host: HTMLDivElement, root: Root;
const spec: PersonaSpec = { display_name: 'Haru', traits: ['gentle'], speaking_style: '', verbosity: 'balanced', interests_text: '', fictional_background: '', additional_style_notes: '', enabled_activity_templates: ['paper_boat', 'cloud_catalog', 'paper_stars'] };
const view = (revision = 0): PersonaView => ({ profile_id: '00000000-0000-0000-0000-000000000001', revision, content_hash: 'hash', spec: structuredClone(spec), policy_version: 'core', presets: [{ id: 'official', spec }, { id: 'gentle', spec: { ...spec, verbosity: 'brief' } }, { id: 'concise', spec: { ...spec, display_name: 'Calm', verbosity: 'brief' } }] });
beforeEach(() => {
  vi.clearAllMocks(); retainPendingPersonaMutation(null); setLocale('zh-CN');
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  vi.mocked(getPersona).mockResolvedValue(view()); vi.mocked(mutatePersona).mockResolvedValue(view(1));
  vi.mocked(getPersonaRevisions).mockResolvedValue({ revisions: [{ ...view(), created_at: 1, operation: 'initial', restored_from_revision: null }] });
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
async function mount() { await act(async () => root.render(<CompanionPersonaSettings />)); }
async function click(text: string) {
  const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.replace(/\s/g, '') === text.replace(/\s/g, ''));
  expect(button, text).toBeTruthy(); await act(async () => button!.click());
}
async function confirm() { const inputs = [...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]; const input = inputs[inputs.length - 1]; await act(async () => input.click()); }
it('loads all three presets as drafts, preserves cancel, and requires explicit save confirmation', async () => {
  await mount();
  for (const preset of ['温和简洁', '冷静直接', '官方默认']) { await click(preset); expect(mutatePersona).not.toHaveBeenCalled(); }
  await click('冷静直接'); expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.value).toBe('Calm');
  await click('取消编辑'); expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.value).toBe('Haru');
  await click('温和简洁'); await click('保存人设'); expect(mutatePersona).not.toHaveBeenCalled();
  await confirm(); await click('保存人设');
  expect(mutatePersona).toHaveBeenCalledWith(expect.objectContaining({ operation: 'update', confirmed: true, expected_revision: 0, spec: expect.objectContaining({ verbosity: 'brief' }) }));
  expect(host.textContent).toContain('表达示例，非实际模型生成效果');
});
it('retains draft on CAS conflict, reloads latest and requires a new confirmation', async () => {
  vi.mocked(mutatePersona).mockRejectedValueOnce(new AxiosError('conflict', '409', undefined, undefined, { status: 409 } as never));
  await mount(); await click('冷静直接'); await confirm(); await click('保存人设');
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.value).toBe('Calm');
  vi.mocked(getPersona).mockResolvedValue(view(3)); await click('读取最新版并保留草稿');
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.value).toBe('Calm');
  await click('保存人设'); expect(mutatePersona).toHaveBeenCalledTimes(1);
  await confirm(); await click('保存人设'); expect(mutatePersona).toHaveBeenLastCalledWith(expect.objectContaining({ expected_revision: 3 }));
});
it('retries a lost response with the identical command, not a new revision request', async () => {
  vi.mocked(mutatePersona).mockRejectedValueOnce(new Error('lost response'));
  await mount(); await click('温和简洁'); await confirm(); await click('保存人设');
  const first = vi.mocked(mutatePersona).mock.calls[0][0];
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.disabled).toBe(true);
  await click('重试同一保存请求'); expect(vi.mocked(mutatePersona).mock.calls[1][0]).toEqual(first);
});
it('loads historical content as draft and restores through append-only mutation', async () => {
  vi.mocked(getPersona).mockResolvedValue(view(2)); await mount(); await click('查看人设历史'); await click('恢复此版本到草稿');
  expect(mutatePersona).not.toHaveBeenCalled(); await confirm(); await click('保存人设');
  expect(mutatePersona).toHaveBeenCalledWith(expect.objectContaining({ operation: 'restore', target_revision: 0, expected_revision: 2 }));
});
it('switches authored UI copy to English without altering persona content', async () => {
  await mount(); await click('冷静直接'); await act(async () => setLocale('en-US'));
  expect(host.textContent).toContain('Companion persona'); expect(host.textContent).toContain('Illustrative example, not actual model output');
  expect(host.querySelector<HTMLInputElement>('[aria-label="Companion name"]')!.value).toBe('Calm');
});
it('shares the character name across consumers without replacing Pilot branding or user text', async () => {
  function Surface() { const name = useCompanionName(); return <div>{name} · Pilot — {companionText('Haru chat', name)}</div>; }
  await act(async () => root.render(<><Surface /><Surface /></>));
  act(() => publishPersona({ ...view(1), spec: { ...spec, display_name: '$& Nova' } }));
  expect(host.textContent).toBe('$& Nova · Pilot — $& Nova chat$& Nova · Pilot — $& Nova chat');
});
it('validates bounds, control characters and empty activities independently from interests', () => {
  expect(validPersona({ ...spec, enabled_activity_templates: [], traits: [] })).toBe(true);
  expect(validPersona({ ...spec, display_name: ' ' })).toBe(false);
  expect(validPersona({ ...spec, traits: Array(6).fill('x') })).toBe(false);
  expect(validPersona({ ...spec, fictional_background: '\u202eoverride' })).toBe(false);
  expect(personaDifferences(spec, { ...spec, interests_text: 'Photography' })).toEqual(['interests_text']);
});

it('allows an empty activity whitelist without touching notification or life settings', async () => {
  await mount();
  const activities = [...host.querySelectorAll<HTMLInputElement>('fieldset input[type="checkbox"]')];
  for (const checkbox of activities) await act(async () => checkbox.click());
  await confirm(); await click('保存人设');
  const command = vi.mocked(mutatePersona).mock.calls[0][0];
  expect(command).toMatchObject({ operation: 'update', spec: { enabled_activity_templates: [] } });
  expect(command).not.toHaveProperty('settings');
});

it('retains an uncertain command across unmount and refuses duplicate rapid saves', async () => {
  let reject!: (error: Error) => void;
  vi.mocked(mutatePersona).mockImplementationOnce(() => new Promise((_, failure) => { reject = failure; }));
  await mount(); await click('温和简洁'); await confirm();
  const save = [...host.querySelectorAll<HTMLButtonElement>('button')].find(item => item.textContent?.replace(/\s/g, '') === '保存人设')!;
  await act(async () => { save.click(); save.click(); });
  expect(mutatePersona).toHaveBeenCalledTimes(1);
  const command = vi.mocked(mutatePersona).mock.calls[0][0];
  act(() => root.unmount()); root = createRoot(host);
  await act(async () => reject(new Error('lost response')));
  vi.mocked(getPersona).mockResolvedValue(view(1)); await mount();
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.disabled).toBe(true);
  await click('重试同一保存请求'); expect(vi.mocked(mutatePersona).mock.calls[1][0]).toEqual(command);
});

it('replays the original command but displays the newer active revision after another window saves', async () => {
  vi.mocked(mutatePersona).mockRejectedValueOnce(new Error('lost response'));
  await mount(); await click('温和简洁'); await confirm(); await click('保存人设');
  const original = vi.mocked(mutatePersona).mock.calls[0][0];
  vi.mocked(mutatePersona).mockResolvedValueOnce(view(1));
  vi.mocked(getPersona).mockResolvedValue({ ...view(2), spec: { ...spec, display_name: 'Newer companion' } });
  await click('重试同一保存请求');
  expect(vi.mocked(mutatePersona).mock.calls[1][0]).toEqual(original);
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.value).toBe('Newer companion');
  expect(host.textContent).toContain('当前人设版本：2');
});
it('distinguishes a confirmed save with failed readback from an unknown mutation outcome', async () => {
  await mount(); await click('温和简洁'); await confirm();
  vi.mocked(getPersona).mockRejectedValueOnce(new Error('readback unavailable'));
  await click('保存人设');
  expect(host.textContent).toContain('保存已确认，暂时无法读取当前人设');
  expect(host.textContent).not.toContain('重试同一保存请求');
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.disabled).toBe(true);
  vi.mocked(getPersona).mockResolvedValue(view(1)); await click('刷新当前人设');
  expect(mutatePersona).toHaveBeenCalledTimes(1); expect(host.textContent).toContain('当前人设版本：1');
});
it('ignores malformed retained persona data rather than crashing or resending it', async () => {
  sessionStorage.setItem('offerpilot.persona.pending-mutation', JSON.stringify({ profile_id: view().profile_id, command_id: 'retained', confirmed: true, expected_revision: 0, operation: 'update', spec: { ...spec, enabled_activity_templates: null } }));
  await mount();
  expect(host.querySelector<HTMLInputElement>('[aria-label="伙伴名字"]')!.disabled).toBe(false);
  expect(mutatePersona).not.toHaveBeenCalled();
});

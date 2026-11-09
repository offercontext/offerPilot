// @vitest-environment jsdom
import { act, StrictMode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import type { TrainingEvidenceDraft, TrainingEvidenceState } from '@/types/trainingEvidence';
const service = vi.hoisted(() => ({ get: vi.fn(), change: vi.fn() }));
vi.mock('@/services/trainingEvidence', async (original) => ({ ...(await original<typeof import('@/services/trainingEvidence')>()), getTrainingEvidence: service.get, changeTrainingEvidence: service.change }));
import TrainingEvidencePanel from './TrainingEvidencePanel';
import { TrainingEvidenceError } from '@/services/trainingEvidence';
import { setLocale } from '@/i18n';

const state = (): TrainingEvidenceState => ({ revision: 0, skill_id: 'idempotency_retry', available: true, reason: 'current', records: [], recommendation: null, scenarios: [{ id: 'original', prompt: 'original' }, { id: 'retry_timeout', prompt: 'retry' }, { id: 'concurrent_retry', prompt: 'concurrent' }] });
const record = { id: 10, scenario_id: 'original' as const, answer: 'saved original answer', assistance: 'unknown' as const, created_at: '2026-11-09T00:00:00Z', updated_at: '2026-11-09T00:00:00Z', withdrawn: false };
let root: Root;
let container: HTMLDivElement;
let retained: TrainingEvidenceDraft | null;
const retain = (value: TrainingEvidenceDraft | null) => { retained = value; return true; };
const render = (planId = 1, draft?: TrainingEvidenceDraft) => act(() => root.render(<TrainingEvidencePanel key={planId} planId={planId} originalAnswer="saved original answer" draft={draft} onDraftChange={retain} />));
const button = (text: string) => [...container.querySelectorAll('button')].find((element) => element.textContent === text)!;
async function click(text: string) { await act(async () => { button(text).click(); }); }
async function open() { await click('选择这个技能并查看记录'); }
async function select(label: string, value: string) {
  const element = container.querySelector(`select[aria-label="${label}"]`) as HTMLSelectElement;
  await act(async () => { element.value = value; element.dispatchEvent(new Event('change', { bubbles: true })); });
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  setLocale('zh-CN'); retained = null;
  service.get.mockReset().mockResolvedValue(state()); service.change.mockReset();
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); setLocale('zh-CN'); });

describe('TrainingEvidencePanel', () => {
  it('loads only after explicit skill selection and saves original answer with unknown assistance', async () => {
    render(); expect(service.get).not.toHaveBeenCalled(); expect(service.change).not.toHaveBeenCalled();
    await open(); expect(service.get).toHaveBeenCalledWith(1);
    expect((container.querySelector('textarea') as HTMLTextAreaElement).readOnly).toBe(true);
    service.change.mockResolvedValue({ ...state(), revision: 1, records: [record] });
    await click('确认保存训练证据');
    expect(service.change.mock.calls[0][1]).toMatchObject({ action: 'record', expected_revision: 0, skill_id: 'idempotency_retry', scenario_id: 'original', answer: 'saved original answer', assistance: 'unknown' });
    expect(retained).toBeNull();
  });
  it('recovers an owned draft under StrictMode without a stuck loading state', async () => {
    const resolvers: Array<(value: TrainingEvidenceState) => void> = [];
    service.get.mockImplementation(() => new Promise<TrainingEvidenceState>((resolve) => { resolvers.push(resolve); }));
    const draft: TrainingEvidenceDraft = { scenario: 'original', answer: 'saved original answer', assistance: 'hinted', editingId: null, command: { action: 'record', expected_revision: 0, idempotency_key: 'original-key', skill_id: 'idempotency_retry', scenario_id: 'original', answer: 'saved original answer', assistance: 'hinted' } };
    act(() => root.render(<StrictMode><TrainingEvidencePanel planId={1} originalAnswer="saved original answer" draft={draft} onDraftChange={retain} /></StrictMode>));
    expect(resolvers).toHaveLength(2);
    await act(async () => { resolvers[0](state()); });
    expect(button('使用原操作重试').disabled).toBe(true);
    await act(async () => { resolvers[1](state()); });
    expect(button('使用原操作重试').disabled).toBe(false);
    service.change.mockResolvedValue({ ...state(), revision: 1, records: [record] });
    await click('使用原操作重试'); expect(service.change.mock.calls[0][1]).toEqual(draft.command);
  });
  it('does not write if the owned command cannot be retained', async () => {
    act(() => root.render(<TrainingEvidencePanel planId={1} originalAnswer="saved original answer" onDraftChange={() => false} />));
    await open(); await click('确认保存训练证据');
    expect(service.change).not.toHaveBeenCalled(); expect(container.textContent).toContain('未自动覆盖');
  });
  it('freezes uncertain writes and recovers original command after owned remount', async () => {
    service.change.mockRejectedValueOnce(new TrainingEvidenceError(503)).mockResolvedValue({ ...state(), revision: 1, records: [record] });
    render(); await open(); await select('这次如何完成回答', 'hinted'); await click('确认保存训练证据');
    const frozen = service.change.mock.calls[0][1]; const draft = retained!;
    expect(draft.command).toEqual(frozen); expect(container.textContent).toContain('输入已冻结');
    expect((container.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
    act(() => root.unmount()); root = createRoot(container); await act(async () => render(1, draft));
    await click('使用原操作重试');
    expect(service.change.mock.calls[1][1]).toEqual(frozen); expect(retained).toBeNull();
  });
  it.each([new TrainingEvidenceError(408), new TrainingEvidenceError(409), new TrainingEvidenceError(401, 'proxy_auth_required')])('retains the original key for an unrecognized transport rejection', async (error) => {
    service.change.mockRejectedValue(error); render(); await open(); await click('确认保存训练证据');
    expect(retained?.command).toEqual(service.change.mock.calls[0][1]); expect(button('使用原操作重试')).toBeTruthy();
  });
  it('does not submit twice from rapid repeated clicks', async () => {
    let resolve!: (value: TrainingEvidenceState) => void;
    service.change.mockReturnValue(new Promise<TrainingEvidenceState>((done) => { resolve = done; }));
    render(); await open();
    await act(async () => { button('确认保存训练证据').click(); button('确认保存训练证据').click(); });
    expect(service.change).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ ...state(), revision: 1, records: [record] }));
  });
  it('rejects stale results after navigating to another plan and preserves old owned command', async () => {
    let resolve!: (value: TrainingEvidenceState) => void;
    service.change.mockReturnValue(new Promise<TrainingEvidenceState>((done) => { resolve = done; }));
    render(); await open(); await click('确认保存训练证据'); const oldDraft = retained;
    render(2); await open(); await act(async () => resolve({ ...state(), revision: 1, records: [record] }));
    expect(container.textContent).not.toContain('#10'); expect(retained).toEqual(oldDraft);
  });
  it('requires reload after a deterministic conflict and never silently overwrites', async () => {
    service.change.mockRejectedValue(new TrainingEvidenceError(409, 'training_evidence_conflict'));
    render(); await open(); await click('确认保存训练证据');
    expect(container.textContent).toContain('未自动覆盖'); expect(retained?.command).toBeNull();
    expect((container.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(true);
    await click('重新读取'); expect((container.querySelector('fieldset') as HTMLFieldSetElement).disabled).toBe(false);
    expect(service.change).toHaveBeenCalledTimes(1);
  });
  it('allows withdrawal after source change and clears answer/draft caches without deleting the original plan', async () => {
    service.get.mockResolvedValue({ ...state(), revision: 1, available: false, reason: 'source_changed', records: [record] });
    service.change.mockResolvedValue({ ...state(), revision: 2, available: false, records: [{ ...record, answer: '', withdrawn: true }] });
    render(); await open(); expect(button('更正记录').disabled).toBe(true); expect(container.querySelector('fieldset')).toBeNull();
    await click('撤回训练记录'); expect(container.textContent).toContain('原计划的完成回答仍保留'); await click('确认撤回训练记录');
    expect(service.change.mock.calls[0][1]).toMatchObject({ action: 'withdraw', record_id: 10, expected_revision: 1 });
    expect(container.textContent).not.toContain('saved original answer'); expect(retained).toBeNull();
  });
  it('corrects assistance without editing original answer and cites selected evidence without mastery claims', async () => {
    service.get.mockResolvedValue({ ...state(), revision: 1, records: [record], recommendation: { scenario_id: 'retry_timeout', prompt: '中文服务端题', reason: '中文服务端理由', comparison: '中文服务端比较', evidence_record_ids: [10] } });
    service.change.mockResolvedValue({ ...state(), revision: 2, records: [{ ...record, assistance: 'reference' }] });
    render(); await open(); expect(container.textContent).toContain('不能由回答质量倒推');
    expect(container.querySelector('a')?.getAttribute('href')).toBe('#training-1-10');
    await click('更正记录'); await select('这次如何完成回答', 'reference'); await click('确认保存更正');
    expect(service.change.mock.calls[0][1]).toMatchObject({ action: 'correct', record_id: 10, answer: record.answer, assistance: 'reference' });
  });
  it('uses localized deterministic advice rather than backend Chinese strings', async () => {
    service.get.mockResolvedValue({ ...state(), records: [record], recommendation: { scenario_id: 'retry_timeout', prompt: '中文题', reason: '中文理由', comparison: '中文比较', evidence_record_ids: [10] } });
    render(); await open(); await act(async () => { await setLocale('en-US'); });
    expect(container.textContent).toContain('cannot be inferred from answer quality'); expect(container.textContent).not.toContain('中文');
  });
});

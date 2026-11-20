import { afterEach, describe, expect, it, vi } from 'vitest';
import { connectMedia, RealtimeVoiceController, type RealtimeAnswer } from './realtimeVoiceController';
import { automaticAcceptance } from '@/services/interviewVoice';
import { createStudioState, reduceStudioState } from '@/features/interviewStudio/interviewStudioController';

const grant = { session_id: 'session', attempt_id: 1, epoch: 3, token: 't'.repeat(64), consent_revision: 'interview-realtime-v1' };
const input = { context: { kind: 'quick_practice' as const, caseId: 2 }, attemptId: 1, turnNo: 1, consentRevision: grant.consent_revision, takeover: false };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function setup(answer = vi.fn<(answer: RealtimeAnswer) => Promise<'saved' | 'unknown' | 'failed'>>().mockResolvedValue('saved')) {
  const media = { send: vi.fn(), mute: vi.fn(), close: vi.fn() };
  const deps = { create: vi.fn().mockResolvedValue(grant), action: vi.fn().mockResolvedValue(undefined), connect: vi.fn().mockResolvedValue(media), key: () => 'immutable-key' };
  const controller = new RealtimeVoiceController({ onState: vi.fn(), onAnswer: answer }, deps);
  return { controller, answer, media, deps };
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('realtime voice ownership and business boundary', () => {
  it('ignores late manual and control messages from an already saved turn', async () => {
    const { controller, media } = setup(); await controller.start(input);
    await controller.receive({ type: 'candidate', turn_no: 1, text: '上一题的答案' });
    controller.questionReady(2);
    for (const type of ['manual', 'control']) {
      await controller.receive({ type, intent: 'manual', turn_no: 1, text: '上一题的续说' });
    }
    expect(controller.state.status).toBe('listening');
    expect(controller.state.transcript).toBe('');
    expect(media.close).not.toHaveBeenCalled();
    await controller.pause();
  });
  it('only submits one immutable candidate for the current turn with the scoped grant', async () => {
    const pending = deferred<'saved'>();
    const { controller, answer } = setup(vi.fn().mockReturnValue(pending.promise));
    await controller.start(input);
    await controller.receive({ type: 'candidate', turn_no: 2, text: 'wrong turn' });
    const request = controller.receive({ type: 'candidate', turn_no: 1, text: '第一份回答' });
    await controller.receive({ type: 'candidate', turn_no: 1, text: '重复候选' });
    expect(answer).toHaveBeenCalledOnce();
    expect(answer).toHaveBeenCalledWith({ turnNo: 1, turnKey: 'immutable-key', text: '第一份回答', acceptance: automaticAcceptance(grant) });
    pending.resolve('saved'); await request;
    await controller.pause();
  });

  it('stops media on pause without cancelling an in-flight business write or resubmitting', async () => {
    const pending = deferred<'saved'>();
    const { controller, answer, media } = setup(vi.fn().mockReturnValue(pending.promise));
    await controller.start(input);
    const request = controller.receive({ type: 'candidate', turn_no: 1, text: '已发出的原文' });
    await controller.pause();
    expect(media.close).toHaveBeenCalledOnce();
    await controller.receive({ type: 'candidate', turn_no: 1, text: '迟到' });
    pending.resolve('saved'); await request;
    expect(controller.state.status).toBe('paused');
    expect(answer).toHaveBeenCalledOnce();
  });

  it('preserves supplemental speech without binding it to a new question', async () => {
    const pending = deferred<'saved'>();
    const { controller, media } = setup(vi.fn().mockReturnValue(pending.promise));
    await controller.start(input);
    const request = controller.receive({ type: 'candidate', turn_no: 1, text: '原文' });
    await controller.receive({ type: 'manual', turn_no: 1, text: '还有一件事', reason: '续说' });
    controller.questionReady(2);
    expect(controller.state.supplemental).toBe(true);
    expect(controller.state.transcript).toBe('还有一件事');
    expect(media.send).not.toHaveBeenCalled();
    pending.resolve('saved'); await request;
  });

  it('replaces partial transcript revisions and ignores stale messages', async () => {
    const { controller } = setup(); await controller.start(input);
    await controller.receive({ type: 'transcript', turn_no: 1, revision: 2, text: '前面' });
    await controller.receive({ type: 'transcript', turn_no: 1, revision: 3, text: '前面的修订' });
    await controller.receive({ type: 'transcript', turn_no: 1, revision: 1, text: '过期' });
    expect(controller.state.transcript).toBe('前面的修订');
    await controller.pause();
  });

  it('keeps manual writes locked until grant revocation is confirmed', async () => {
    const { controller, deps } = setup(); await controller.start(input);
    deps.action.mockRejectedValueOnce(new Error('offline'));
    await controller.pause();
    expect(controller.state.locked).toBe(true);
    await controller.pause();
    expect(controller.state.locked).toBe(false);
  });

  it('closes a grant returned after a cancelled start without opening the microphone', async () => {
    const creation = deferred<typeof grant>();
    const { controller, deps } = setup(); deps.create.mockReturnValueOnce(creation.promise);
    const start = controller.start(input);
    await controller.pause(); creation.resolve(grant); await start;
    expect(deps.connect).not.toHaveBeenCalled();
    expect(deps.action).toHaveBeenCalledWith(grant, 'close');
  });

  it('pauses on an unknown answer result and retains text for recovery', async () => {
    const { controller } = setup(vi.fn().mockResolvedValue('unknown')); await controller.start(input);
    await controller.receive({ type: 'candidate', turn_no: 1, text: '不能改动的原文' });
    expect(controller.state.status).toBe('manual');
    expect(controller.state.transcript).toBe('不能改动的原文');
    expect(controller.state.message).toContain('原尝试恢复');
  });

  it('ignores an expired heartbeat from a prior generation after reopen', async () => {
    vi.useFakeTimers();
    const { controller, deps, media } = setup();
    let reject!: (reason: Error) => void;
    const abandoned = new Promise<void>((_resolve, deny) => { reject = deny; });
    await controller.start(input);
    deps.action.mockReturnValueOnce(abandoned);
    await vi.advanceTimersByTimeAsync(10000);
    await controller.pause();
    const replacement = { ...grant, session_id: 'replacement' };
    deps.create.mockResolvedValueOnce(replacement);
    await controller.start(input);
    reject(new Error('old lease expired'));
    await Promise.resolve();
    expect(controller.state.status).toBe('listening');
    expect(media.close).toHaveBeenCalledOnce();
    expect(deps.action).not.toHaveBeenCalledWith(replacement, 'close');
    await controller.pause();
  });

  it('does not ask for microphone access when WebRTC setup throws', async () => {
    const close = vi.fn();
    const getUserMedia = vi.fn();
    vi.stubGlobal('RTCPeerConnection', class {
      close = close;
      createDataChannel() { throw new Error('cannot create data channel'); }
    });
    vi.stubGlobal('Audio', class {});
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
    await expect(connectMedia(grant, vi.fn(), vi.fn(), new AbortController().signal))
      .rejects.toThrow('cannot create data channel');
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(close).toHaveBeenCalledOnce();
  });

  it('expires media when heartbeats fail', async () => {
    vi.useFakeTimers();
    const { controller, deps, media } = setup(); await controller.start(input);
    deps.action.mockRejectedValueOnce(new Error('revoked'));
    await vi.advanceTimersByTimeAsync(10000);
    expect(media.close).toHaveBeenCalled();
    expect(controller.state.locked).toBe(false);
  });

  it('persists original auto acceptance across answer recovery and clears it for the next question', () => {
    const original = reduceStudioState(createStudioState({ turnNo: 1, question: '题目' }), {
      type: 'answer_submitting', turnKey: 'original', answer: '原文', acceptance: automaticAcceptance(grant),
    });
    const unknown = reduceStudioState(original, { type: 'result_unknown', operation: 'answer', message: '断线' });
    const restored = JSON.parse(JSON.stringify(unknown));
    const retry = reduceStudioState(restored, { type: 'answer_submitting', turnKey: restored.turnKey });
    expect(retry.answerAcceptance).toEqual(automaticAcceptance(grant));
    expect(retry.answer).toBe('原文');
    expect(retry.turnKey).toBe('original');
    expect(reduceStudioState(retry, { type: 'question_succeeded', turnNo: 2, question: '下一题' }).answerAcceptance).toBeUndefined();
  });
});

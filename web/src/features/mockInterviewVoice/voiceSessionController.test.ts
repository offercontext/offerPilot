import { describe, expect, it, vi } from 'vitest';
import { createVoiceSessionController, type VoiceSessionState } from './voiceSessionController';

function frame(seconds: number, amplitude = 0.08): Float32Array {
  return new Float32Array(seconds * 10).fill(amplitude);
}

describe('voice session controller', () => {
  it('serializes 20 second chunks, overlaps two seconds and merges interim text', async () => {
    let releaseFirst!: (value: string) => void;
    const first = new Promise<string>((resolve) => { releaseFirst = resolve; });
    const transcribe = vi.fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce('完成回滚并复盘');
    const interim: string[] = [];
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe,
      onState: vi.fn(),
      onInterimTranscript: (text) => interim.push(text),
      chunkMs: 20_000,
      overlapMs: 2_000,
    });

    controller.start(7, 10);
    controller.acceptFrame(frame(20), 0);
    controller.acceptFrame(frame(18), 20_000);
    expect(transcribe).toHaveBeenCalledTimes(1);
    releaseFirst('我先定位日志，完成回滚');
    for (let turn = 0; turn < 6; turn += 1) await Promise.resolve();
    expect(transcribe).toHaveBeenCalledTimes(2);
    expect((transcribe.mock.calls[1][0] as Float32Array).length).toBe(200);
    for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
    expect(interim[interim.length - 1]).toBe('我先定位日志，完成回滚并复盘');
  });

  it('waits for every already queued segment before final review', async () => {
    let releaseFirst!: (value: string) => void;
    let releaseSecond!: (value: string) => void;
    const first = new Promise<string>((resolve) => { releaseFirst = resolve; });
    const second = new Promise<string>((resolve) => { releaseSecond = resolve; });
    const transcribe = vi.fn()
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second);
    const states: VoiceSessionState[] = [];
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe,
      onState: (state) => states.push(state),
      onInterimTranscript: vi.fn(),
      chunkMs: 1_000,
      overlapMs: 0,
    });

    controller.start(1, 10);
    controller.acceptFrame(frame(1), 0);
    controller.acceptFrame(frame(1), 1_000);
    let finished = false;
    const finishing = controller.finish().then(() => { finished = true; });

    releaseFirst('第一段');
    for (let turn = 0; turn < 4; turn += 1) await Promise.resolve();
    expect(transcribe).toHaveBeenCalledTimes(2);
    expect(finished).toBe(false);

    releaseSecond('第二段');
    await finishing;
    expect(states[states.length - 1]).toMatchObject({ status: 'reviewing' });
  });

  it('switches to batch mode under backlog and never starts concurrent inference', async () => {
    let release!: (value: string) => void;
    const transcribe = vi.fn(() => new Promise<string>((resolve) => { release = resolve; }));
    const states: VoiceSessionState[] = [];
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe,
      onState: (state) => states.push(state),
      onInterimTranscript: vi.fn(),
      chunkMs: 1_000,
      overlapMs: 100,
    });
    controller.start(1, 10);
    controller.acceptFrame(frame(1), 0);
    controller.acceptFrame(frame(1), 1_000);
    controller.acceptFrame(frame(1), 2_000);

    expect(transcribe).toHaveBeenCalledTimes(1);
    expect(controller.getMode()).toBe('batch');
    expect(states[states.length - 1]).toMatchObject({ status: 'recording', transcriptionMode: 'batch' });
    release('第一段');
    await Promise.resolve();
    expect(transcribe).toHaveBeenCalledTimes(1);
  });

  it('fences stale results and caps recording at five minutes without confirming', async () => {
    let release!: (value: string) => void;
    const transcribe = vi.fn(() => new Promise<string>((resolve) => { release = resolve; }));
    const states: VoiceSessionState[] = [];
    const interim = vi.fn();
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe,
      onState: (state) => states.push(state),
      onInterimTranscript: interim,
      chunkMs: 1_000,
      overlapMs: 100,
      maxDurationMs: 2_000,
    });
    controller.start(1, 10);
    controller.acceptFrame(frame(1), 0);
    controller.cancel();
    controller.start(2, 10);
    release('旧结果');
    await Promise.resolve();
    expect(interim).not.toHaveBeenCalledWith('旧结果');

    controller.acceptFrame(frame(2), 0);
    expect(states[states.length - 1]?.status).toBe('finalizing');
  });

  it('does not publish a review after the session is disposed while finalizing', async () => {
    let release!: (value: string) => void;
    const transcribe = vi.fn(() => new Promise<string>((resolve) => { release = resolve; }));
    const states: VoiceSessionState[] = [];
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe,
      onState: (state) => states.push(state),
      onInterimTranscript: vi.fn(),
      chunkMs: 1_000,
      overlapMs: 0,
    });
    controller.start(1, 10);
    controller.acceptFrame(frame(1), 0);
    const finishing = controller.finish();
    controller.dispose();
    release('late transcript');
    await finishing;

    expect(states.some((state) => state.status === 'reviewing')).toBe(false);
  });

  it('tracks voiced ranges and long pauses without ending the answer', () => {
    const states: VoiceSessionState[] = [];
    const controller = createVoiceSessionController({
      now: () => 0,
      transcribe: vi.fn(async () => ''),
      onState: (state) => states.push(state),
      onInterimTranscript: vi.fn(),
      calibrationMs: 0,
    });
    controller.start(1, 10);
    controller.acceptFrame(frame(1, 0.1), 0);
    controller.acceptFrame(frame(3, 0), 1_000);

    expect(states.some((state) => state.status === 'speech_paused')).toBe(true);
    expect(controller.getVoicedRanges()).toEqual([[0, 1_000]]);
  });
});

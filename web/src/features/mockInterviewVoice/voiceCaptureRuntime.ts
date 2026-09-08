export type VoiceCaptureFrame = {
  pcm?: Float32Array;
  sampleRate: number;
  atMs: number;
  durationMs: number;
  rms: number;
  peak: number;
};

type ConnectableNode = { connect(node: unknown): unknown; disconnect(): void };
type AnalyserLike = ConnectableNode & {
  fftSize: number;
  getFloatTimeDomainData(values: Float32Array): void;
};
type WorkletNodeLike = ConnectableNode & { port: { onmessage: ((event: MessageEvent) => void) | null } };
type AudioContextLike = {
  sampleRate: number;
  currentTime: number;
  audioWorklet?: { addModule(url: string): Promise<void> };
  createMediaStreamSource(stream: MediaStream): ConnectableNode;
  createAnalyser(): AnalyserLike;
  close(): Promise<void>;
};

export type VoiceCaptureRuntimeDependencies = {
  createAudioContext: () => AudioContextLike;
  createWorkletNode: (context: AudioContextLike) => WorkletNodeLike;
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (handle: number) => void;
  now: () => number;
  workletUrl: string;
};

export type VoiceCaptureRuntime = {
  readonly batchOnly: boolean;
  pause(): void;
  resume(): void;
  dispose(): Promise<void>;
};

function defaultDependencies(): VoiceCaptureRuntimeDependencies {
  return {
    createAudioContext: () => new AudioContext() as unknown as AudioContextLike,
    createWorkletNode: (context) => new AudioWorkletNode(context as unknown as BaseAudioContext, 'offerpilot-voice-activity', {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      processorOptions: { frameSamples: Math.max(160, Math.round(context.sampleRate * 0.02)) },
    }) as unknown as WorkletNodeLike,
    requestFrame: (callback) => requestAnimationFrame(callback),
    cancelFrame: (handle) => cancelAnimationFrame(handle),
    now: () => performance.now(),
    workletUrl: new URL('./voiceActivity.worklet.ts', import.meta.url).toString(),
  };
}

function levels(samples: Float32Array): { rms: number; peak: number } {
  let squares = 0;
  let peak = 0;
  for (const raw of samples) {
    const sample = Number.isFinite(raw) ? raw : 0;
    squares += sample * sample;
    peak = Math.max(peak, Math.abs(sample));
  }
  return { rms: samples.length ? Math.sqrt(squares / samples.length) : 0, peak };
}

export async function createVoiceCaptureRuntime(
  stream: MediaStream,
  onFrame: (frame: VoiceCaptureFrame) => void,
  dependencies: VoiceCaptureRuntimeDependencies = defaultDependencies(),
): Promise<VoiceCaptureRuntime> {
  const context = dependencies.createAudioContext();
  let source: ConnectableNode | undefined;
  let worklet: WorkletNodeLike | undefined;
  let analyser: AnalyserLike | undefined;
  let animationHandle: number | undefined;
  let disposed = false;
  let paused = false;
  let batchOnly = true;
  try {
    source = context.createMediaStreamSource(stream);
    if (context.audioWorklet) {
      try {
        await context.audioWorklet.addModule(dependencies.workletUrl);
        if (disposed) throw new Error('voice capture disposed');
        worklet = dependencies.createWorkletNode(context);
        worklet.port.onmessage = (event) => {
          if (disposed || paused) return;
          const data = event.data as Partial<VoiceCaptureFrame>;
          if (!(data.pcm instanceof Float32Array)) return;
          onFrame({
            pcm: data.pcm,
            sampleRate: context.sampleRate,
            atMs: Number.isFinite(data.atMs) ? Number(data.atMs) : dependencies.now(),
            durationMs: Number.isFinite(data.durationMs) ? Number(data.durationMs) : data.pcm.length / context.sampleRate * 1_000,
            rms: Number.isFinite(data.rms) ? Number(data.rms) : levels(data.pcm).rms,
            peak: Number.isFinite(data.peak) ? Number(data.peak) : levels(data.pcm).peak,
          });
        };
        source.connect(worklet);
        batchOnly = false;
      } catch {
        worklet?.disconnect();
        worklet = undefined;
      }
    }
    if (batchOnly) {
      analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      let previousAt = dependencies.now();
      const sample = () => {
        if (disposed) return;
        const currentAt = dependencies.now();
        if (!paused) {
          analyser!.getFloatTimeDomainData(samples);
          const measured = levels(samples);
          onFrame({ sampleRate: context.sampleRate, atMs: currentAt, durationMs: Math.max(0, currentAt - previousAt), ...measured });
        }
        previousAt = currentAt;
        animationHandle = dependencies.requestFrame(sample);
      };
      animationHandle = dependencies.requestFrame(sample);
    }
  } catch (error) {
    source?.disconnect();
    worklet?.disconnect();
    analyser?.disconnect();
    await context.close().catch(() => undefined);
    throw error;
  }

  return {
    get batchOnly() { return batchOnly; },
    pause() { paused = true; },
    resume() { paused = false; },
    async dispose() {
      if (disposed) return;
      disposed = true;
      if (animationHandle !== undefined) dependencies.cancelFrame(animationHandle);
      if (worklet) worklet.port.onmessage = null;
      worklet?.disconnect();
      analyser?.disconnect();
      source?.disconnect();
      await context.close().catch(() => undefined);
    },
  };
}

export type ContinuousVoiceStatus =
  | 'disabled'
  | 'preflight'
  | 'reading_question'
  | 'waiting_for_speech'
  | 'listening'
  | 'end_candidate'
  | 'transcribing'
  | 'reviewing_transcript'
  | 'submitting_confirmed_answer'
  | 'generating_next_question'
  | 'paused'
  | 'fallback_standard'
  | 'result_unknown'
  | 'completed'
  | 'closed';

export type ContinuousVoiceState = {
  status: ContinuousVoiceStatus;
  generation: number;
  question: string;
  transcript: string;
  countdownSeconds: number | null;
  error: string | null;
};

export type ContinuousVoiceCommand =
  | { type: 'preflight'; generation: number }
  | { type: 'read_question'; generation: number; question: string }
  | { type: 'start_recording'; generation: number }
  | { type: 'start_end_countdown'; generation: number; durationMs: number }
  | { type: 'cancel_end_countdown'; generation: number }
  | { type: 'stop_recording'; generation: number }
  | { type: 'start_transcription'; generation: number }
  | { type: 'submit_answer'; generation: number; text: string }
  | { type: 'generate_next_question'; generation: number }
  | { type: 'pause_capture'; generation: number }
  | { type: 'resume_capture'; generation: number }
  | { type: 'cleanup'; generation: number };

type TimerHandle = unknown;

export type ContinuousVoiceSessionDependencies = {
  onState: (state: ContinuousVoiceState) => void;
  onCommand: (command: ContinuousVoiceCommand) => void;
  schedule?: (delayMs: number, callback: () => void) => TimerHandle;
  cancel?: (handle: TimerHandle) => void;
  endCandidateMs?: number;
};

export type ContinuousVoiceSessionController = {
  enable(question: string): void;
  disable(generation?: number): void;
  preflightSucceeded(generation?: number): void;
  preflightFailed(message: string, generation?: number): void;
  questionReadFinished(generation?: number): void;
  skipReading(generation?: number): void;
  speechDetected(generation?: number): void;
  silenceDetected(generation?: number): void;
  cancelEndCandidate(generation?: number): void;
  countdownElapsed(generation?: number): void;
  manualStop(generation?: number): void;
  recordingLimitReached(generation?: number): void;
  recordingStopped(generation?: number): void;
  recordingReset(generation?: number): void;
  transcriptReady(text: string, generation?: number): void;
  transcriptionFailed(message: string, generation?: number): void;
  confirmTranscript(text: string, generation?: number): void;
  answerSubmissionSucceeded(hasNextQuestion?: boolean, generation?: number): void;
  answerSubmissionUnknown(message: string, generation?: number): void;
  retryAnswerSubmission(generation?: number): void;
  nextQuestionReady(question: string, generation?: number): void;
  nextQuestionUnknown(message: string, generation?: number): void;
  retryNextQuestion(generation?: number): void;
  pause(generation?: number): void;
  resume(generation?: number): void;
  fallback(message: string, generation?: number): void;
  complete(generation?: number): void;
  close(generation?: number): void;
  getSnapshot(): ContinuousVoiceState;
  subscribe(listener: (state: ContinuousVoiceState) => void): () => void;
};

const DEFAULT_END_CANDIDATE_MS = 3_000;

function defaultSchedule(delayMs: number, callback: () => void): TimerHandle {
  return window.setTimeout(callback, delayMs);
}

function defaultCancel(handle: TimerHandle): void {
  window.clearTimeout(handle as number);
}

function initialState(): ContinuousVoiceState {
  return {
    status: 'disabled',
    generation: 0,
    question: '',
    transcript: '',
    countdownSeconds: null,
    error: null,
  };
}

export function createContinuousVoiceSessionController(
  dependencies: ContinuousVoiceSessionDependencies,
): ContinuousVoiceSessionController {
  const schedule = dependencies.schedule ?? defaultSchedule;
  const cancel = dependencies.cancel ?? defaultCancel;
  const listeners = new Set<(state: ContinuousVoiceState) => void>();
  const endCandidateMs = dependencies.endCandidateMs ?? DEFAULT_END_CANDIDATE_MS;
  let state = initialState();
  let countdown: TimerHandle | undefined;
  let resumeNeedsPreflight = false;

  const emit = () => {
    const next = { ...state };
    dependencies.onState(next);
    for (const listener of listeners) listener(next);
  };

  const clearCountdown = () => {
    if (countdown !== undefined) cancel(countdown);
    countdown = undefined;
  };

  const command = (next: ContinuousVoiceCommand) => dependencies.onCommand(next);

  const isCurrent = (generation?: number) => (
    state.status !== 'closed' && (generation === undefined || generation === state.generation)
  );

  const invalidate = () => {
    clearCountdown();
    state = { ...state, generation: state.generation + 1, countdownSeconds: null };
  };

  const setState = (next: Partial<ContinuousVoiceState>) => {
    state = { ...state, ...next };
    emit();
  };

  const stopForReview = (generation?: number) => {
    if (!isCurrent(generation)) return;
    if (!['waiting_for_speech', 'listening', 'end_candidate'].includes(state.status)) return;
    clearCountdown();
    setState({ status: 'transcribing', countdownSeconds: null, error: null });
    command({ type: 'stop_recording', generation: state.generation });
  };

  const controller: ContinuousVoiceSessionController = {
    enable(question) {
      if (state.status === 'closed') return;
      invalidate();
      resumeNeedsPreflight = false;
      setState({ status: 'preflight', question, transcript: '', error: null });
      command({ type: 'preflight', generation: state.generation });
    },
    disable(generation) {
      if (!isCurrent(generation)) return;
      invalidate();
      setState({ status: 'disabled', transcript: '', countdownSeconds: null, error: null });
      command({ type: 'cleanup', generation: state.generation });
    },
    preflightSucceeded(generation) {
      if (!isCurrent(generation) || state.status !== 'preflight') return;
      setState({ status: 'reading_question', error: null });
      command({ type: 'read_question', generation: state.generation, question: state.question });
    },
    preflightFailed(message, generation) {
      if (!isCurrent(generation) || state.status !== 'preflight') return;
      invalidate();
      setState({ status: 'fallback_standard', error: message, countdownSeconds: null });
      command({ type: 'cleanup', generation: state.generation });
    },
    questionReadFinished(generation) {
      if (!isCurrent(generation) || state.status !== 'reading_question') return;
      setState({ status: 'waiting_for_speech', error: null });
      command({ type: 'start_recording', generation: state.generation });
    },
    skipReading(generation) {
      if (!isCurrent(generation) || state.status !== 'reading_question') return;
      setState({ status: 'waiting_for_speech', error: null });
      command({ type: 'start_recording', generation: state.generation });
    },
    speechDetected(generation) {
      if (!isCurrent(generation)) return;
      if (state.status === 'waiting_for_speech') {
        setState({ status: 'listening', error: null });
        return;
      }
      if (state.status === 'end_candidate') {
        clearCountdown();
        setState({ status: 'listening', countdownSeconds: null, error: null });
        command({ type: 'cancel_end_countdown', generation: state.generation });
      }
    },
    silenceDetected(generation) {
      if (!isCurrent(generation) || state.status !== 'listening' || countdown !== undefined) return;
      setState({ status: 'end_candidate', countdownSeconds: Math.ceil(endCandidateMs / 1_000) });
      command({ type: 'start_end_countdown', generation: state.generation, durationMs: endCandidateMs });
      const scheduledGeneration = state.generation;
      let remainingSeconds = Math.ceil(endCandidateMs / 1_000);
      const tick = () => {
        if (!isCurrent(scheduledGeneration) || state.status !== 'end_candidate') return;
        remainingSeconds -= 1;
        if (remainingSeconds <= 0) {
          controller.countdownElapsed(scheduledGeneration);
          return;
        }
        setState({ countdownSeconds: remainingSeconds });
        countdown = schedule(1_000, tick);
      };
      countdown = schedule(Math.min(1_000, endCandidateMs), tick);
    },
    cancelEndCandidate(generation) {
      if (!isCurrent(generation) || state.status !== 'end_candidate') return;
      clearCountdown();
      setState({ status: 'listening', countdownSeconds: null });
      command({ type: 'cancel_end_countdown', generation: state.generation });
    },
    countdownElapsed(generation) {
      if (!isCurrent(generation) || state.status !== 'end_candidate') return;
      clearCountdown();
      setState({ status: 'transcribing', countdownSeconds: null, error: null });
      command({ type: 'stop_recording', generation: state.generation });
    },
    manualStop(generation) { stopForReview(generation); },
    recordingLimitReached(generation) { stopForReview(generation); },
    recordingStopped(generation) {
      if (!isCurrent(generation)) return;
      if (['waiting_for_speech', 'listening', 'end_candidate'].includes(state.status)) {
        clearCountdown();
        setState({ status: 'transcribing', countdownSeconds: null, error: null });
        command({ type: 'start_transcription', generation: state.generation });
        return;
      }
      if (state.status !== 'transcribing') return;
      command({ type: 'start_transcription', generation: state.generation });
    },
    recordingReset(generation) {
      if (!isCurrent(generation) || !['transcribing', 'reviewing_transcript'].includes(state.status)) return;
      clearCountdown();
      setState({ status: 'waiting_for_speech', transcript: '', countdownSeconds: null, error: null });
      command({ type: 'start_recording', generation: state.generation });
    },
    transcriptReady(text, generation) {
      if (!isCurrent(generation) || state.status !== 'transcribing') return;
      setState({ status: 'reviewing_transcript', transcript: text, error: null });
    },
    transcriptionFailed(message, generation) {
      if (!isCurrent(generation) || state.status !== 'transcribing') return;
      setState({ status: 'reviewing_transcript', error: message });
    },
    confirmTranscript(text, generation) {
      const confirmed = text.trim();
      if (!isCurrent(generation) || state.status !== 'reviewing_transcript' || !confirmed) return;
      setState({ status: 'submitting_confirmed_answer', transcript: confirmed, error: null });
      command({ type: 'submit_answer', generation: state.generation, text: confirmed });
    },
    answerSubmissionSucceeded(hasNextQuestion = true, generation) {
      if (!isCurrent(generation) || state.status !== 'submitting_confirmed_answer') return;
      if (!hasNextQuestion) {
        setState({ status: 'completed', countdownSeconds: null });
        return;
      }
      setState({ status: 'generating_next_question', countdownSeconds: null });
      command({ type: 'generate_next_question', generation: state.generation });
    },
    answerSubmissionUnknown(message, generation) {
      if (!isCurrent(generation) || state.status !== 'submitting_confirmed_answer') return;
      setState({ status: 'result_unknown', error: message });
    },
    retryAnswerSubmission(generation) {
      if (!isCurrent(generation) || state.status !== 'result_unknown') return;
      setState({ status: 'submitting_confirmed_answer', error: null });
    },
    nextQuestionReady(question, generation) {
      if (!isCurrent(generation) || state.status !== 'generating_next_question') return;
      setState({ status: 'reading_question', question, transcript: '', error: null });
      command({ type: 'read_question', generation: state.generation, question });
    },
    nextQuestionUnknown(message, generation) {
      if (!isCurrent(generation) || state.status !== 'generating_next_question') return;
      setState({ status: 'result_unknown', error: message });
    },
    retryNextQuestion(generation) {
      if (!isCurrent(generation) || state.status !== 'result_unknown') return;
      setState({ status: 'generating_next_question', error: null });
    },
    pause(generation) {
      if (!isCurrent(generation) || !['preflight', 'reading_question', 'waiting_for_speech', 'listening', 'end_candidate'].includes(state.status)) return;
      resumeNeedsPreflight = state.status === 'preflight';
      invalidate();
      setState({ status: 'paused', countdownSeconds: null });
      command({ type: 'pause_capture', generation: state.generation });
    },
    resume(generation) {
      if (!isCurrent(generation) || state.status !== 'paused') return;
      invalidate();
      if (resumeNeedsPreflight) {
        resumeNeedsPreflight = false;
        setState({ status: 'preflight', error: null });
        command({ type: 'preflight', generation: state.generation });
        return;
      }
      setState({ status: 'reading_question', error: null });
      command({ type: 'resume_capture', generation: state.generation });
      command({ type: 'read_question', generation: state.generation, question: state.question });
    },
    fallback(message, generation) {
      if (!isCurrent(generation)) return;
      if (state.status === 'fallback_standard') {
        controller.disable(generation);
        return;
      }
      invalidate();
      setState({ status: 'fallback_standard', error: message, countdownSeconds: null });
      command({ type: 'cleanup', generation: state.generation });
    },
    complete(generation) {
      if (!isCurrent(generation)) return;
      clearCountdown();
      setState({ status: 'completed', countdownSeconds: null });
    },
    close(generation) {
      if (state.status === 'closed' || (generation !== undefined && generation !== state.generation)) return;
      invalidate();
      setState({ status: 'closed', countdownSeconds: null });
      command({ type: 'cleanup', generation: state.generation });
    },
    getSnapshot: () => ({ ...state }),
    subscribe(listener) {
      listeners.add(listener);
      listener({ ...state });
      return () => listeners.delete(listener);
    },
  };

  return controller;
}

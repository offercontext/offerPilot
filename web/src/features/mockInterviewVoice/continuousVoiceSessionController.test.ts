import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createContinuousVoiceSessionController,
  type ContinuousVoiceCommand,
  type ContinuousVoiceSessionController,
  type ContinuousVoiceState,
} from './continuousVoiceSessionController';

type ScheduledJob = { delayMs: number; callback: () => void; cancelled: boolean };

function createHarness() {
  const states: ContinuousVoiceState[] = [];
  const commands: ContinuousVoiceCommand[] = [];
  const jobs: ScheduledJob[] = [];
  const dependencies = {
    onState: (state: ContinuousVoiceState) => { states.push(state); },
    onCommand: (command: ContinuousVoiceCommand) => { commands.push(command); },
    schedule: vi.fn((delayMs: number, callback: () => void) => {
      const job = { delayMs, callback, cancelled: false };
      jobs.push(job);
      return job;
    }),
    cancel: vi.fn((job: unknown) => { (job as ScheduledJob).cancelled = true; }),
  };
  const flushCountdown = () => {
    const job = jobs[jobs.length - 1];
    if (!job || job.cancelled) throw new Error('missing active countdown');
    job.callback();
  };
  return { states, commands, jobs, dependencies, flushCountdown };
}

function startListening(controller: ContinuousVoiceSessionController) {
  controller.enable('筱哲的问题');
  controller.preflightSucceeded();
  controller.questionReadFinished();
  controller.speechDetected();
}

describe('continuous voice session controller', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('reads, waits for speech, counts down only after speech, and stops without submitting', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    controller.enable('筱哲的问题');
    expect(events.states[events.states.length - 1]?.status).toBe('preflight');
    controller.preflightSucceeded();
    expect(events.commands.map((item) => item.type)).toEqual(['preflight', 'read_question']);
    controller.questionReadFinished();
    expect(events.states[events.states.length - 1]?.status).toBe('waiting_for_speech');
    controller.speechDetected();
    controller.silenceDetected();
    expect(events.states[events.states.length - 1]?.status).toBe('end_candidate');
    expect(events.commands[events.commands.length - 1]?.type).toBe('start_end_countdown');
    events.flushCountdown();
    events.flushCountdown();
    events.flushCountdown();
    expect(events.commands[events.commands.length - 1]?.type).toBe('stop_recording');
    expect(events.commands.some((item) => item.type === 'submit_answer')).toBe(false);
  });

  it('does not end a silent answer and cancels the countdown when speech resumes', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    controller.enable('筱哲的问题');
    controller.preflightSucceeded();
    controller.questionReadFinished();
    expect(events.states[events.states.length - 1]?.status).toBe('waiting_for_speech');
    controller.silenceDetected();
    expect(events.states[events.states.length - 1]?.status).toBe('waiting_for_speech');
    expect(events.jobs).toHaveLength(0);

    controller.speechDetected();
    controller.silenceDetected();
    const countdown = events.jobs[events.jobs.length - 1]!;
    controller.speechDetected();
    expect(events.states[events.states.length - 1]?.status).toBe('listening');
    expect(countdown.cancelled).toBe(true);
    expect(events.commands.some((item) => item.type === 'stop_recording')).toBe(false);
  });

  it('shows a cancellable three-second countdown before stopping locally', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.silenceDetected();
    events.flushCountdown();
    expect(events.states[events.states.length - 1]?.countdownSeconds).toBe(2);
    events.flushCountdown();
    expect(events.states[events.states.length - 1]?.countdownSeconds).toBe(1);
    events.flushCountdown();
    expect(events.commands[events.commands.length - 1]?.type).toBe('stop_recording');
  });

  it('requires review and explicit confirmation before submitting once', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.manualStop();
    expect(events.states[events.states.length - 1]?.status).toBe('transcribing');
    expect(events.commands[events.commands.length - 1]?.type).toBe('stop_recording');
    controller.confirmTranscript('不应绕过确认界面');
    expect(events.commands.some((item) => item.type === 'submit_answer')).toBe(false);

    controller.recordingStopped();
    controller.transcriptReady('我先定位日志，再完成回滚。');
    expect(events.states[events.states.length - 1]?.status).toBe('reviewing_transcript');
    controller.confirmTranscript('我先定位日志，再完成回滚。');
    controller.confirmTranscript('重复提交不应发生');
    expect(events.commands.filter((item) => item.type === 'submit_answer')).toHaveLength(1);
    expect(events.states[events.states.length - 1]?.status).toBe('submitting_confirmed_answer');
  });

  it('generates and reads the next question only after confirmed answer success', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.manualStop();
    controller.recordingStopped();
    controller.transcriptReady('已确认回答');
    controller.confirmTranscript('已确认回答');
    controller.answerSubmissionSucceeded();

    expect(events.states[events.states.length - 1]?.status).toBe('generating_next_question');
    expect(events.commands[events.commands.length - 1]?.type).toBe('generate_next_question');
    controller.nextQuestionReady('下一道问题');
    expect(events.states[events.states.length - 1]?.status).toBe('reading_question');
    expect(events.commands[events.commands.length - 1]?.type).toBe('read_question');
  });

  it('uses the five-minute limit as a local stop, never as a submit', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.recordingLimitReached();

    expect(events.states[events.states.length - 1]?.status).toBe('transcribing');
    expect(events.commands[events.commands.length - 1]?.type).toBe('stop_recording');
    expect(events.commands.some((item) => item.type === 'submit_answer')).toBe(false);
  });

  it('moves to transcription when the browser stops capture unexpectedly', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.recordingStopped();

    expect(events.states[events.states.length - 1]?.status).toBe('transcribing');
    expect(events.commands[events.commands.length - 1]?.type).toBe('start_transcription');
  });

  it('can restart capture after a cancelled transcript or a rerecord request', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.manualStop();
    controller.recordingStopped();
    controller.transcriptReady('');
    controller.recordingReset();

    expect(events.states[events.states.length - 1]?.status).toBe('waiting_for_speech');
    expect(events.commands[events.commands.length - 1]?.type).toBe('start_recording');
  });

  it('resumes the correct continuous operation after a result-unknown retry', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    startListening(controller);
    controller.manualStop();
    controller.recordingStopped();
    controller.transcriptReady('confirmed answer');
    controller.confirmTranscript('confirmed answer');
    controller.answerSubmissionUnknown('answer unknown');
    controller.retryAnswerSubmission();
    controller.answerSubmissionSucceeded();
    expect(events.states[events.states.length - 1]?.status).toBe('generating_next_question');

    controller.nextQuestionUnknown('question unknown');
    controller.retryNextQuestion();
    expect(events.states[events.states.length - 1]?.status).toBe('generating_next_question');
  });

  it('pauses a pending microphone preflight and requires authorization again on resume', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    controller.enable('pending microphone question');
    controller.pause();
    expect(events.states[events.states.length - 1]?.status).toBe('paused');
    controller.resume();
    expect(events.states[events.states.length - 1]?.status).toBe('preflight');
    expect(events.commands[events.commands.length - 1]?.type).toBe('preflight');
  });

  it('can be re-enabled after falling back to standard mode', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    controller.enable('fallback question');
    controller.fallback('microphone unavailable');
    expect(events.states[events.states.length - 1]?.status).toBe('fallback_standard');
    controller.fallback('try again');
    expect(events.states[events.states.length - 1]?.status).toBe('disabled');
    controller.enable('retry question');
    expect(events.states[events.states.length - 1]?.status).toBe('preflight');
  });

  it('ignores late callbacks from a closed generation and cleans the countdown', () => {
    const events = createHarness();
    const controller = createContinuousVoiceSessionController(events.dependencies);

    controller.enable('筱哲的问题');
    const oldGeneration = events.states[events.states.length - 1]!.generation;
    controller.preflightSucceeded();
    controller.questionReadFinished();
    controller.speechDetected();
    controller.silenceDetected();
    controller.close();
    const commandCount = events.commands.length;

    controller.preflightSucceeded(oldGeneration);
    controller.questionReadFinished(oldGeneration);
    controller.transcriptReady('迟到文字');

    expect(events.states[events.states.length - 1]?.status).toBe('closed');
    expect(events.commands).toHaveLength(commandCount);
    expect(events.dependencies.cancel).toHaveBeenCalledTimes(1);
  });
});

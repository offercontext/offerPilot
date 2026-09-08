import { describe, expect, it, vi } from 'vitest';
import {
  createLocalSpeechRecognition,
  detectVoiceInterviewCapabilities,
  ensureLocalSpeechLanguage,
  queryLocalSpeechLanguage,
  type SpeechRecognitionConstructorLike,
} from './voiceInterviewCapability';

function recognitionConstructor(overrides: Record<string, unknown> = {}): SpeechRecognitionConstructorLike {
  const recognition = {
    processLocally: false,
    lang: '',
    continuous: true,
    interimResults: false,
    start: vi.fn(),
    stop: vi.fn(),
    abort: vi.fn(),
    ...overrides,
  };
  return Object.assign(vi.fn(() => recognition), {
    available: vi.fn(),
    install: vi.fn(),
  }) as unknown as SpeechRecognitionConstructorLike;
}

describe('voice interview browser capabilities', () => {
  it('reports recording, speech synthesis and local recognition independently', () => {
    const SpeechRecognition = recognitionConstructor();

    expect(detectVoiceInterviewCapabilities({
      MediaRecorder: function MediaRecorder() {},
      speechSynthesis: { speak: vi.fn() },
      SpeechRecognition,
    })).toEqual({ recorder: true, speechSynthesis: true, localRecognition: true });
    expect(detectVoiceInterviewCapabilities({})).toEqual({
      recorder: false,
      speechSynthesis: false,
      localRecognition: false,
    });
  });

  it('requires the local-only contract before exposing recognition', () => {
    const SpeechRecognition = recognitionConstructor({ processLocally: undefined });

    expect(detectVoiceInterviewCapabilities({ SpeechRecognition })).toEqual({
      recorder: false,
      speechSynthesis: false,
      localRecognition: false,
    });
  });

  it.each([
    ['available', 'available'],
    ['downloadable', 'downloadable'],
    ['downloading', 'downloading'],
    ['unavailable', 'unavailable'],
  ] as const)('normalizes %s local language availability', async (browserStatus, expected) => {
    const SpeechRecognition = recognitionConstructor();
    SpeechRecognition.available = vi.fn().mockResolvedValue(browserStatus);

    await expect(queryLocalSpeechLanguage(SpeechRecognition, 'zh-CN')).resolves.toBe(expected);
    expect(SpeechRecognition.available).toHaveBeenCalledWith({ langs: ['zh-CN'], processLocally: true });
  });

  it('fails closed when local language availability cannot be proved', async () => {
    const SpeechRecognition = recognitionConstructor();
    SpeechRecognition.available = vi.fn().mockRejectedValue(new Error('blocked'));

    await expect(queryLocalSpeechLanguage(SpeechRecognition, 'zh-CN')).resolves.toBe('unavailable');
    await expect(queryLocalSpeechLanguage(undefined, 'zh-CN')).resolves.toBe('unavailable');
  });

  it('installs only an explicitly requested local language pack', async () => {
    const SpeechRecognition = recognitionConstructor();
    SpeechRecognition.install = vi.fn().mockResolvedValue(true);

    await expect(ensureLocalSpeechLanguage(SpeechRecognition, 'zh-CN')).resolves.toBe(true);
    expect(SpeechRecognition.install).toHaveBeenCalledWith({ langs: ['zh-CN'] });
  });

  it('creates recognition with local processing and Chinese fixed before start', () => {
    const SpeechRecognition = recognitionConstructor();
    const recognition = createLocalSpeechRecognition(SpeechRecognition, 'zh-CN');

    expect(recognition.processLocally).toBe(true);
    expect(recognition.lang).toBe('zh-CN');
    expect(recognition.continuous).toBe(true);
    expect(recognition.interimResults).toBe(true);
  });
});

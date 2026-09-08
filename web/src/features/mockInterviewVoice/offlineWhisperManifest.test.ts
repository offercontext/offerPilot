import { describe, expect, it } from 'vitest';
import { OFFLINE_WHISPER_MANIFEST, validateOfflineWhisperManifest } from './offlineWhisperManifest';

describe('offline Whisper manifest', () => {
  it('pins the approved multilingual model to an immutable revision', () => {
    expect(OFFLINE_WHISPER_MANIFEST.modelId).toBe('onnx-community/whisper-small');
    expect(OFFLINE_WHISPER_MANIFEST.revision).toMatch(/^[0-9a-f]{40}$/);
    expect(OFFLINE_WHISPER_MANIFEST.revision).not.toBe('main');
    expect(OFFLINE_WHISPER_MANIFEST.maxAudioSeconds).toBe(300);
    expect(OFFLINE_WHISPER_MANIFEST.approximateBytes).toBeGreaterThan(150 * 1024 * 1024);
    expect(validateOfflineWhisperManifest(OFFLINE_WHISPER_MANIFEST)).toBe(true);
  });

  it('rejects floating revisions and untrusted hosts', () => {
    expect(validateOfflineWhisperManifest({ ...OFFLINE_WHISPER_MANIFEST, revision: 'main' })).toBe(false);
    expect(validateOfflineWhisperManifest({ ...OFFLINE_WHISPER_MANIFEST, sourceUrl: 'https://example.com/model' })).toBe(false);
  });
});

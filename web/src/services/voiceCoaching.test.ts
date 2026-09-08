import { beforeEach, describe, expect, it, vi } from 'vitest';

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock('./http', () => ({ createApiClient: () => http }));

const {
  deleteVoiceCoachingSnapshot,
  getVoiceCoachingSnapshot,
  getVoiceCoachingTrends,
  listVoiceCoachingSnapshots,
  saveVoiceCoachingSnapshot,
} = await import('./voiceCoaching');

beforeEach(() => {
  http.get.mockReset();
  http.post.mockReset();
  http.delete.mockReset();
});

describe('voice coaching service', () => {
  it('uses turn ownership for immutable snapshot save and read', async () => {
    http.post.mockResolvedValue({ data: { id: 9 } });
    http.get.mockResolvedValue({ data: { id: 9 } });
    const payload = {
      idempotency_key: 'voice-web-save-key-0001',
      total_duration_ms: 72_000,
      voiced_duration_ms: 25_000,
      pause_count: 1,
      longest_pause_ms: 3_000,
      speech_rate_cpm: 118,
      filler_occurrences: [{ text: '然后', count: 1, transcript_offsets: [0] }],
      reflection_text: '下次先给结论。',
      focus_kind: 'long_pause_control' as const,
      origin_snapshot_id: null,
    };

    await saveVoiceCoachingSnapshot({ applicationId: 1, eventId: 2, attemptId: 3, turnNo: 4, payload });
    await getVoiceCoachingSnapshot({ applicationId: 1, eventId: 2, attemptId: 3, turnNo: 4 });

    const path = '/applications/1/events/2/mock-interview/attempts/3/turns/4/voice-coaching-snapshot';
    expect(http.post).toHaveBeenCalledWith(path, payload);
    expect(http.get).toHaveBeenCalledWith(path);
    expect(JSON.stringify(http.post.mock.calls)).not.toContain('audio');
  });

  it('uses interview-scoped history, trends, cursor, and delete routes', async () => {
    http.get.mockResolvedValueOnce({ data: { items: [] } }).mockResolvedValueOnce({ data: { snapshot_count: 0 } });
    http.delete.mockResolvedValue({});

    await listVoiceCoachingSnapshots({ limit: 10, beforeId: 8 });
    await getVoiceCoachingTrends();
    await deleteVoiceCoachingSnapshot(7);

    expect(http.get).toHaveBeenNthCalledWith(1, '/interview/voice-coaching/snapshots', {
      params: { limit: 10, before_id: 8 },
    });
    expect(http.get).toHaveBeenNthCalledWith(2, '/interview/voice-coaching/trends');
    expect(http.delete).toHaveBeenCalledWith('/interview/voice-coaching/snapshots/7');
  });
});

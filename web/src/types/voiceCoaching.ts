export type VoiceCoachingFocusKind = 'long_pause_control' | 'filler_reduction' | 'pace_consistency';

export interface VoiceCoachingFillerOccurrence {
  text: string;
  count: number;
  transcript_offsets: number[];
}

export interface VoiceCoachingSnapshotCreate {
  idempotency_key: string;
  total_duration_ms: number;
  voiced_duration_ms: number;
  pause_count: number;
  longest_pause_ms: number;
  speech_rate_cpm: number | null;
  filler_occurrences: VoiceCoachingFillerOccurrence[];
  reflection_text: string;
  focus_kind: VoiceCoachingFocusKind | null;
  origin_snapshot_id: number | null;
}

export interface VoiceCoachingSnapshot {
  id: number;
  attempt_id: number;
  turn_id: number;
  application_id: number;
  event_id: number;
  question_text: string;
  confirmed_answer_text: string;
  answer_sha256: string;
  measurement_source: 'local_browser_measurement';
  total_duration_ms: number;
  voiced_duration_ms: number;
  pause_count: number;
  longest_pause_ms: number;
  speech_rate_cpm: number | null;
  filler_occurrences: VoiceCoachingFillerOccurrence[];
  reflection_text: string;
  focus_kind: VoiceCoachingFocusKind | null;
  origin_snapshot_id: number | null;
  created_at: string;
  source_available: boolean;
  company_name: string;
  position_name: string;
}

export interface VoiceCoachingMetricWindow {
  current_median: number | null;
  previous_median: number | null;
  delta: number | null;
  source_snapshot_ids: number[];
  previous_source_snapshot_ids: number[];
}

export interface VoiceCoachingRecommendation {
  focus_kind: VoiceCoachingFocusKind;
  title: string;
  reason: string;
  source_snapshot_ids: number[];
  source_snapshot_id: number;
  application_id: number;
  event_id: number;
  question_text: string;
  source_available: boolean;
}

export interface VoiceCoachingTrends {
  snapshot_count: number;
  window_size: number;
  metrics: Record<'total_duration_ms' | 'longest_pause_ms' | 'speech_rate_cpm' | 'filler_per_minute', VoiceCoachingMetricWindow>;
  recommendation: VoiceCoachingRecommendation | null;
}

export interface VoiceCoachingPendingReview {
  turnNo: number;
  summary: {
    totalDurationMs: number;
    voicedDurationMs: number;
    pauseCount: number;
    longestPauseMs: number;
    speechRateCpm?: number;
    fillerOccurrences: Array<{ text: string; count: number; transcriptOffsets: number[] }>;
  };
  reflectionText: string;
  focusKind: VoiceCoachingFocusKind | null;
  originSnapshotId: number | null;
  idempotencyKey: string | null;
  saveState: 'idle' | 'saving' | 'unknown' | 'conflict' | 'saved';
  snapshotId: number | null;
}

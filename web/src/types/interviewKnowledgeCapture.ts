export type CapturePreviewStatus =
  | 'not_requested'
  | 'direct_ready'
  | 'ai_generating'
  | 'ai_ready'
  | 'safe_empty'
  | 'provider_unknown'
  | 'confirm_unknown'
  | 'confirmed';

export interface SelectedFragment {
  fragment_id: string;
  path: '/questions' | '/self_reflection' | '/difficulty_points' | '/mood';
  start: number;
  end: number;
  text: string;
}

export interface CaptureEvidenceRef {
  fragment_id: string;
  excerpt: string;
}

export interface CapturePreviewBlock {
  block_id: string;
  text: string;
  evidence_refs: CaptureEvidenceRef[];
}

export interface CapturePreview {
  title: string;
  blocks: CapturePreviewBlock[];
}

export interface InterviewKnowledgeCaptureAttempt {
  attempt_key: string;
  note_fingerprint: string;
  selected_fragments: SelectedFragment[];
  preview_status: CapturePreviewStatus;
  preview: CapturePreview;
  error_code?: string | null;
}

export interface ConfirmedInterviewKnowledge {
  version_id: number;
  note_id: number;
  source_id: number;
  content: CapturePreview;
  evidence: Array<{ id: string; path: string; excerpt: string }>;
}

export interface InterviewKnowledgeNote {
  id: number;
  title: string;
  origin_kind: 'confirmed_interview_capture';
  version_id?: number;
  version_number?: number;
  content?: CapturePreview;
  source_id?: number;
  source_status?: 'frozen' | 'source_changed';
  captured_at?: string;
  evidence?: Array<{
    id: string;
    path: string;
    excerpt: string;
    char_start?: number;
    char_end?: number;
    line_start?: number;
    line_end?: number;
    frozen_at?: string;
  }>;
}

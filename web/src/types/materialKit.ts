export type MaterialKitStatus = 'draft' | 'ready' | 'submitted';

export type EditableMaterialKitStatus = 'draft' | 'ready';

export interface MaterialKitResumeAdvice {
  summary: string;
  highlights: string[];
  rewrite_bullets: string[];
  gaps: string[];
  notes: string;
}

export interface MaterialKitMessage {
  type: 'recruiter_email' | 'referral_message' | 'application_note' | string;
  title: string;
  body: string;
  notes: string;
}

export interface MaterialKitChecklistItem {
  id: string;
  label: string;
  done: boolean;
}

export interface MaterialKitContent {
  resume_advice: MaterialKitResumeAdvice;
  messages: MaterialKitMessage[];
  checklist: MaterialKitChecklistItem[];
}

export interface ApplicationMaterialKit {
  id: number;
  application_id: number;
  resume_id?: number;
  jd_analysis_id?: number;
  jd_version_id?: number;
  jd_snapshot: string;
  status: MaterialKitStatus;
  content_json: string;
  created_at: string;
  updated_at: string;
}

export interface MaterialKitViewModel extends Omit<ApplicationMaterialKit, 'content_json'> {
  content: MaterialKitContent;
}

export interface GenerateMaterialKitInput {
  resume_id: number;
  jd_version_id: number;
  jd_analysis_id?: number;
  overwrite?: boolean;
}

export interface UpdateMaterialKitInput {
  resume_id?: number;
  jd_analysis_id?: number;
  jd_snapshot: string;
  jd_version_id?: number;
  status?: EditableMaterialKitStatus;
  content_json: MaterialKitContent;
}

export type MaterialProposalStatus = 'draft' | 'accepted' | 'rejected';

export type MaterialEvidenceSource = 'resume' | 'evidence_bundle' | 'user_assertion';

export interface MaterialEvidenceRef {
  source: MaterialEvidenceSource;
  path: string;
  excerpt: string;
}

export interface MaterialRevisionChange {
  id: string;
  path: string;
  before: string;
  after: string;
  rationale: string;
  evidence_refs: MaterialEvidenceRef[];
}

export interface MaterialRevisionProposalSummary {
  id: number;
  application_id: number;
  material_kit_id: number;
  source_resume_id: number | null;
  status: MaterialProposalStatus;
  summary: string;
  proposal_sha256: string;
  result_resume_id: number | null;
  created_at: string;
}

export interface MaterialRevisionProposal extends MaterialRevisionProposalSummary {
  changes: MaterialRevisionChange[];
  source: {
    application: { id: number; company_name: string; position_name: string };
    material_kit: { id: number; jd_excerpt: string };
    resume: { id: number; title: string };
    latest_evidence_bundle: { id: number; bundle_sha256: string } | null;
    user_assertions: Array<{ id: string; text: string }>;
  };
  accepted_change_ids: string[];
  accepted_at: string | null;
  rejected_at: string | null;
}

export interface CreateMaterialRevisionProposalInput {
  instructions: string;
  user_assertions: string[];
}

export interface AcceptMaterialRevisionProposalInput {
  expected_proposal_sha256: string;
  selected_change_ids: string[];
}

export interface AcceptMaterialRevisionProposalResponse {
  proposal: MaterialRevisionProposal;
  result_resume: Record<string, unknown>;
}

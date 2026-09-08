export interface EvidenceBundleApplicationSource {
  id: number;
  company_name: string;
  position_name: string;
  job_url: string;
  source: string;
}

export interface EvidenceBundleReadySources {
  application: EvidenceBundleApplicationSource;
  jd: {
    sha256: string;
    characters: number;
  };
  resume: {
    id: number;
    title: string;
    sha256: string;
  };
  material_kit: {
    id: number;
    sha256: string;
  };
}

export type EvidenceBundlePreview =
  | {
      application_id: number;
      ready: true;
      issues: string[];
      bundle_sha256: string;
      sources: EvidenceBundleReadySources;
    }
  | {
      application_id: number;
      ready: false;
      issues: string[];
      sources: Record<string, never>;
    };

export interface EvidenceBundleSummary {
  id: number;
  application_id: number;
  sequence: number;
  submitted_at: string;
  confirmed_at: string;
  confirmation_kind: string;
  bundle_sha256: string;
  created_at: string;
}

export interface EvidenceBundleDetail extends EvidenceBundleSummary {
  snapshot: Record<string, unknown>;
}

export interface ConfirmEvidenceBundleInput {
  submitted_at: string;
  idempotency_key: string;
  expected_bundle_sha256: string;
}

export type ApplicationJdVersionSummary = {
  id: number;
  application_id: number;
  version_number: number;
  content_sha256: string;
  source_url: string | null;
  source_kind: 'ui' | 'pilot';
  utf8_byte_length: number;
  preview: string;
  created_at: string;
};

export type ApplicationJdVersion = ApplicationJdVersionSummary & {
  jd_text: string;
};

export type CurrentApplicationJd = {
  current: ApplicationJdVersion | null;
};

export type ApplicationJdVersionSaveInput = {
  jd_text: string;
  source_url: string | null;
  expected_current_version_id: number | null;
  idempotency_key: string;
};

export type ApplicationJdDraft = {
  jdText: string;
  sourceUrl: string;
  expectedCurrentVersionId: number | null;
  idempotencyKey: string | null;
  resultUnknown: boolean;
  pendingOperation: 'save' | null;
};

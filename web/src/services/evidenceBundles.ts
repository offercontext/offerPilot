import type {
  ConfirmEvidenceBundleInput,
  EvidenceBundleDetail,
  EvidenceBundlePreview,
  EvidenceBundleSummary,
} from '@/types/evidenceBundle';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

export async function getEvidenceBundlePreview(applicationID: number): Promise<EvidenceBundlePreview> {
  const { data } = await http.get<EvidenceBundlePreview>(`/applications/${applicationID}/evidence-bundles/preview`);
  return data;
}

export async function confirmEvidenceBundle(
  applicationID: number,
  input: ConfirmEvidenceBundleInput,
): Promise<EvidenceBundleDetail> {
  const { data } = await http.post<EvidenceBundleDetail>(`/applications/${applicationID}/evidence-bundles`, input);
  return data;
}

export async function listEvidenceBundles(applicationID: number): Promise<EvidenceBundleSummary[]> {
  const { data } = await http.get<EvidenceBundleSummary[]>(`/applications/${applicationID}/evidence-bundles`);
  return data;
}

export async function getEvidenceBundle(applicationID: number, bundleID: number): Promise<EvidenceBundleDetail> {
  const { data } = await http.get<EvidenceBundleDetail>(`/applications/${applicationID}/evidence-bundles/${bundleID}`);
  return data;
}

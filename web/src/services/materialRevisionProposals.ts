import type {
  AcceptMaterialRevisionProposalInput,
  AcceptMaterialRevisionProposalResponse,
  CreateMaterialRevisionProposalInput,
  MaterialRevisionProposal,
  MaterialRevisionProposalSummary,
} from '@/types/materialRevisionProposal';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 130000 });

export async function createMaterialRevisionProposal(
  applicationID: number,
  input: CreateMaterialRevisionProposalInput,
): Promise<MaterialRevisionProposal> {
  const { data } = await http.post<MaterialRevisionProposal>(
    `/applications/${applicationID}/material-revision-proposals`,
    input,
  );
  return data;
}

export async function listMaterialRevisionProposals(
  applicationID: number,
): Promise<MaterialRevisionProposalSummary[]> {
  const { data } = await http.get<MaterialRevisionProposalSummary[]>(
    `/applications/${applicationID}/material-revision-proposals`,
  );
  return data;
}

export async function getMaterialRevisionProposal(
  applicationID: number,
  proposalID: number,
): Promise<MaterialRevisionProposal> {
  const { data } = await http.get<MaterialRevisionProposal>(
    `/applications/${applicationID}/material-revision-proposals/${proposalID}`,
  );
  return data;
}

export async function acceptMaterialRevisionProposal(
  applicationID: number,
  proposalID: number,
  input: AcceptMaterialRevisionProposalInput,
): Promise<AcceptMaterialRevisionProposalResponse> {
  const { data } = await http.post<AcceptMaterialRevisionProposalResponse>(
    `/applications/${applicationID}/material-revision-proposals/${proposalID}/accept`,
    input,
  );
  return data;
}

export async function rejectMaterialRevisionProposal(
  applicationID: number,
  proposalID: number,
): Promise<MaterialRevisionProposal> {
  const { data } = await http.post<MaterialRevisionProposal>(
    `/applications/${applicationID}/material-revision-proposals/${proposalID}/reject`,
  );
  return data;
}

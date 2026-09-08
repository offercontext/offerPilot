import type {
  ApplicationJdVersion,
  ApplicationJdVersionSaveInput,
  ApplicationJdVersionSummary,
  CurrentApplicationJd,
} from '../types/applicationJdVersion';

const request = async <T>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload?.error ?? '岗位资料请求失败') as Error & {
      status?: number;
      code?: string;
    };
    error.status = response.status;
    error.code = payload?.error_code;
    throw error;
  }
  return payload as T;
};

export const getCurrentApplicationJd = (applicationId: number) =>
  request<CurrentApplicationJd>(`/api/applications/${applicationId}/job-description`);

export const listApplicationJdVersions = (applicationId: number, offset = 0, limit = 50) =>
  request<ApplicationJdVersionSummary[]>(
    `/api/applications/${applicationId}/job-description/versions?offset=${offset}&limit=${limit}`,
  );

export const getApplicationJdVersion = (applicationId: number, versionId: number) =>
  request<ApplicationJdVersion>(
    `/api/applications/${applicationId}/job-description/versions/${versionId}`,
  );

export const saveApplicationJdVersion = (
  applicationId: number,
  input: ApplicationJdVersionSaveInput,
) =>
  request<ApplicationJdVersion>(
    `/api/applications/${applicationId}/job-description/versions`,
    { method: 'POST', body: JSON.stringify(input) },
  );

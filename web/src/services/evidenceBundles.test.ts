import { describe, expect, it, vi } from 'vitest';
import type { EvidenceBundleDetail, EvidenceBundlePreview } from '@/types/evidenceBundle';

const { apiGet, apiPost, createApiClient } = vi.hoisted(() => ({
  apiGet: vi.fn(),
  apiPost: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));

createApiClient.mockReturnValue({ get: apiGet, post: apiPost });

const {
  confirmEvidenceBundle,
  getEvidenceBundle,
  getEvidenceBundlePreview,
  listEvidenceBundles,
} = await import('./evidenceBundles');

describe('evidence bundle service', () => {
  it('uses the evidence bundle API endpoints without transforming payloads', async () => {
    const preview: EvidenceBundlePreview = {
      application_id: 7,
      ready: true,
      issues: [],
      bundle_sha256: 'a'.repeat(64),
      sources: {
        application: {
          id: 7,
          company_name: 'Example Co.',
          position_name: 'Software Engineer',
          job_url: 'https://example.com/jobs/7',
          source: 'manual',
        },
        jd: { sha256: 'b'.repeat(64), characters: 1200 },
        resume: { id: 11, title: 'Backend Resume', sha256: 'c'.repeat(64) },
        material_kit: { id: 5, sha256: 'd'.repeat(64) },
      },
    };
    const detail: EvidenceBundleDetail = {
      id: 3,
      application_id: 7,
      sequence: 1,
      confirmed_at: '2026-07-14T09:00:00.000Z',
      submitted_at: '2026-07-14T09:00:00.000Z',
      confirmation_kind: 'user_asserted',
      bundle_sha256: 'a'.repeat(64),
      created_at: '2026-07-14T09:00:00.000Z',
      snapshot: {},
    };
    const input = {
      submitted_at: '2026-07-14T09:00:00.000Z',
      idempotency_key: '87a596a7-3ac2-4f7e-a557-3d18e3d9d554',
      expected_bundle_sha256: 'a'.repeat(64),
    };
    apiGet.mockResolvedValueOnce({ data: preview });
    apiPost.mockResolvedValueOnce({ data: detail });
    apiGet.mockResolvedValueOnce({ data: [detail] });
    apiGet.mockResolvedValueOnce({ data: detail });

    await expect(getEvidenceBundlePreview(7)).resolves.toEqual(preview);
    await expect(confirmEvidenceBundle(7, input)).resolves.toEqual(detail);
    await expect(listEvidenceBundles(7)).resolves.toEqual([detail]);
    await expect(getEvidenceBundle(7, 3)).resolves.toEqual(detail);

    expect(apiGet).toHaveBeenNthCalledWith(1, '/applications/7/evidence-bundles/preview');
    expect(apiPost).toHaveBeenCalledWith('/applications/7/evidence-bundles', input);
    expect(apiGet).toHaveBeenNthCalledWith(2, '/applications/7/evidence-bundles');
    expect(apiGet).toHaveBeenNthCalledWith(3, '/applications/7/evidence-bundles/3');
  });

  it('exposes a hash only after a preview is ready', () => {
    const preview: EvidenceBundlePreview = {
      application_id: 7,
      ready: false,
      issues: ['Material kit is missing'],
      sources: {},
    };

    expect(preview.sources).toEqual({});
    // @ts-expect-error A not-ready preview cannot have a hash to confirm.
    void preview.bundle_sha256;
  });
});

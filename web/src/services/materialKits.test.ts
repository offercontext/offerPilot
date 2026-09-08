import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApplicationMaterialKit, MaterialKitContent, UpdateMaterialKitInput } from '@/types/materialKit';

const { apiPut, createApiClient } = vi.hoisted(() => ({
  apiPut: vi.fn(),
  createApiClient: vi.fn(),
}));

vi.mock('./http', () => ({ createApiClient }));

createApiClient.mockReturnValue({ put: apiPut });

const { updateMaterialKit } = await import('./materialKits');

const content: MaterialKitContent = {
  resume_advice: { summary: '', highlights: [], rewrite_bullets: [], gaps: [], notes: '' },
  messages: [],
  checklist: [],
};

const rawKit: ApplicationMaterialKit = {
  id: 3,
  application_id: 7,
  jd_snapshot: 'Job description',
  status: 'submitted',
  content_json: JSON.stringify(content),
  created_at: '2026-07-14T09:00:00.000Z',
  updated_at: '2026-07-14T09:00:00.000Z',
};

beforeEach(() => {
  apiPut.mockReset();
  apiPut.mockResolvedValue({ data: rawKit });
});

describe('updateMaterialKit', () => {
  it('omits an undefined status so legacy submitted kits are not rewritten', async () => {
    await expect(
      updateMaterialKit(3, {
        jd_snapshot: 'Job description',
        status: undefined,
        content_json: content,
      }),
    ).resolves.toMatchObject({ id: 3, status: 'submitted' });

    expect(apiPut).toHaveBeenCalledWith('/material-kits/3', {
      jd_snapshot: 'Job description',
      content_json: content,
    });
    expect(apiPut.mock.calls[0]?.[1]).not.toHaveProperty('status');
  });

  it('keeps editable statuses in update payloads', async () => {
    await updateMaterialKit(3, {
      jd_snapshot: 'Job description',
      status: 'ready',
      content_json: content,
    });

    expect(apiPut).toHaveBeenCalledWith('/material-kits/3', {
      jd_snapshot: 'Job description',
      status: 'ready',
      content_json: content,
    });
  });

  it('does not permit confirmation-only submitted status inputs', () => {
    const input: UpdateMaterialKitInput = {
      jd_snapshot: 'Job description',
      // @ts-expect-error Submitted status belongs to evidence bundle confirmation.
      status: 'submitted',
      content_json: content,
    };

    expect(input.content_json).toBe(content);
  });
});

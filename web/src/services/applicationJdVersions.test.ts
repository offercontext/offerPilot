import { describe, expect, it, vi } from 'vitest';

import {
  getApplicationJdVersion,
  saveApplicationJdVersion,
} from './applicationJdVersions';

describe('application JD version service', () => {
  it('sends the frozen expected version and never sends source_kind', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ id: 3 }), { status: 201 }),
    );

    await saveApplicationJdVersion(7, {
      jd_text: '岗位资料',
      source_url: null,
      expected_current_version_id: 2,
      idempotency_key: 'jd-api-key-000001',
    });

    const [, request] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(request?.body))).toEqual({
      jd_text: '岗位资料',
      source_url: null,
      expected_current_version_id: 2,
      idempotency_key: 'jd-api-key-000001',
    });
    expect(String(request?.body)).not.toContain('source_kind');
    fetchMock.mockRestore();
  });

  it('uses a scoped detail route and preserves stable error codes', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: '来源已变化', error_code: 'application_jd_stale_current_version' }), {
        status: 409,
      }),
    );

    await expect(getApplicationJdVersion(7, 3)).rejects.toMatchObject({
      status: 409,
      code: 'application_jd_stale_current_version',
    });
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/applications/7/job-description/versions/3',
      expect.anything(),
    );
    vi.restoreAllMocks();
  });
});

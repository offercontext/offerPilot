import { beforeEach, describe, expect, it, vi } from 'vitest';
const state = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock('./http', () => ({ createApiClient: () => ({ post: state.post }) }));
import { createApplicationWithJd } from './applications';

const request = { company_name: '公司', position_name: '岗位', idempotency_key: 'create-test-key-0001', initial_jd: null };
beforeEach(() => state.post.mockReset());
describe('Application creation receipt', () => {
  it('sends the original request and accepts a durable replay', async () => {
    const data = { id: 4, company_name: '公司', position_name: '岗位', jd_version_id: null };
    state.post.mockResolvedValue({ status: 200, data });
    expect(await createApplicationWithJd(request)).toEqual(data);
    expect(state.post).toHaveBeenCalledWith('/applications', request);
  });
  it.each([
    { status: 202, data: { id: 4 } },
    { status: 201, data: null },
    { status: 201, data: { company_name: '公司', position_name: '岗位' } },
    { status: 201, data: { id: 4, company_name: '公司', position_name: '岗位', jd_version_id: '5' } },
  ])('rejects an incomplete response as unknown: %j', async (response) => {
    state.post.mockResolvedValue(response);
    await expect(createApplicationWithJd(request)).rejects.toThrow('创建回执不完整');
  });
});

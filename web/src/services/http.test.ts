// @vitest-environment jsdom
import axios from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createApiClient } from './http';
import { setStoredAuthToken } from './authToken';
import { checkApplicationDuplicates, createApplicationWithJd, listApplications } from './applications';
import { uploadResume } from './resumes';
import { uploadKnowledgeBundle } from './knowledge';

// Keep the real Axios browser adapter, transforms and interceptors. Only the
// transport is fake: these requests must never reach a backend or provider.
class FakeXHR {
  static requests: FakeXHR[] = [];
  static status = 200;
  static data: unknown = [];
  static failure: 'timeout' | 'network' | undefined;
  method = '';
  url = '';
  timeout = 0;
  status = FakeXHR.status;
  statusText = 'Fixture';
  responseText = JSON.stringify(FakeXHR.data);
  headers: Record<string, string> = {};
  body: unknown;
  onloadend: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onerror: ((event: ProgressEvent) => void) | null = null;
  open(method: string, url: string) { this.method = method; this.url = url; }
  setRequestHeader(name: string, value: string) { this.headers[name.toLowerCase()] = value; }
  getAllResponseHeaders() { return 'content-type: application/json\r\n'; }
  abort() {}
  send(body: unknown) {
    this.body = body;
    FakeXHR.requests.push(this);
    queueMicrotask(() => {
      if (FakeXHR.failure === 'timeout') this.ontimeout?.();
      else if (FakeXHR.failure === 'network') this.onerror?.(new ProgressEvent('error'));
      else this.onloadend?.();
    });
  }
}

beforeEach(() => {
  FakeXHR.requests = [];
  FakeXHR.status = 200;
  FakeXHR.data = [];
  FakeXHR.failure = undefined;
  vi.stubGlobal('XMLHttpRequest', FakeXHR);
  localStorage.clear();
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('real Axios browser API contract', () => {
  it('encodes application filters and duplicate queries without changing values', async () => {
    await expect(listApplications('interview & offer')).resolves.toEqual([]);
    await checkApplicationDuplicates({ company_name: 'A&B 中文', position_name: 'C++ / UI', job_url: 'https://example.invalid/job?q=1&x=2' });
    const requests = FakeXHR.requests.map(request => new URL(request.url, 'http://localhost'));
    expect(requests[0].pathname).toBe('/api/applications');
    expect(requests[0].searchParams.get('status')).toBe('interview & offer');
    expect(requests[1].searchParams.get('company_name')).toBe('A&B 中文');
    expect(requests[1].searchParams.get('position_name')).toBe('C++ / UI');
    expect(requests[1].searchParams.get('job_url')).toBe('https://example.invalid/job?q=1&x=2');
    expect(FakeXHR.requests[0].timeout).toBe(10000);
  });

  it('keeps JSON, current auth tokens, explicit headers and response validation', async () => {
    setStoredAuthToken('fixture-token-one');
    const client = createApiClient({ baseURL: '/api', timeout: 10000 });
    await client.post('/fixture', { title: '中文 & +', nested: { active: true } }, { headers: { 'X-Fixture': 'kept' } });
    expect(FakeXHR.requests[0].headers).toMatchObject({ 'x-offerpilot-token': 'fixture-token-one', 'x-fixture': 'kept', 'content-type': 'application/json' });
    expect(JSON.parse(FakeXHR.requests[0].body as string)).toEqual({ title: '中文 & +', nested: { active: true } });
    setStoredAuthToken('fixture-token-two');
    FakeXHR.status = 201;
    FakeXHR.data = { id: 3, company_name: 'Fixture', position_name: 'Engineer', jd_version_id: null };
    await expect(createApplicationWithJd({ company_name: 'Fixture', position_name: 'Engineer', idempotency_key: 'fixture-request', initial_jd: null })).resolves.toEqual(FakeXHR.data);
    expect(FakeXHR.requests[1].headers['x-offerpilot-token']).toBe('fixture-token-two');
    FakeXHR.data = { id: 3 };
    await expect(createApplicationWithJd({ company_name: 'Fixture', position_name: 'Engineer', idempotency_key: 'fixture-request', initial_jd: null })).rejects.toThrow('创建回执不完整');
  });

  it('preserves native resume and multi-file knowledge FormData and browser boundary handling', async () => {
    const file = new File(['fixture'], 'resume.txt', { type: 'text/plain' });
    await uploadResume(file);
    const resume = FakeXHR.requests[0];
    expect(resume.body).toBeInstanceOf(FormData);
    expect((resume.body as FormData).get('file')).toBe(file);
    expect(resume.headers['content-type']).toBeUndefined();
    expect(resume.timeout).toBe(30000);
    await uploadKnowledgeBundle(file, [new File(['asset'], 'asset.txt')], 'Fixture title');
    const form = FakeXHR.requests[1].body as FormData;
    expect(form.get('title_hint')).toBe('Fixture title');
    expect((form.getAll('files')[0] as File).name).toBe('asset.txt');
  });

  it.each([['timeout', 'ECONNABORTED'], ['network', 'ERR_NETWORK']] as const)('preserves %s failures without retrying', async (failure, code) => {
    FakeXHR.failure = failure;
    await expect(createApiClient({ timeout: 12 }).get('/fixture')).rejects.toMatchObject({ isAxiosError: true, code });
    expect(FakeXHR.requests).toHaveLength(1);
  });

  it('preserves HTTP error status and JSON response data', async () => {
    FakeXHR.status = 409;
    FakeXHR.data = { error_code: 'fixture_conflict' };
    await expect(createApiClient().get('/fixture')).rejects.toMatchObject({ isAxiosError: true, response: { status: 409, data: FakeXHR.data } });
  });
});

describe('GHSA-x97p-jq2g-jp4f form serializer regression', () => {
  it('ignores shared-prototype visitors and depth limits in GET params and multipart forms', () => {
    const visited: unknown[] = [];
    const descriptors = Object.getOwnPropertyDescriptors(Object.prototype);
    try {
      Object.defineProperty(Object.prototype, 'visitor', { configurable: true, writable: true, value: (...args: unknown[]) => { visited.push(args); return false; } });
      Object.defineProperty(Object.prototype, 'maxDepth', { configurable: true, writable: true, value: 1 });
      const value = { profile: { nested: { title: 'fixture' } }, labels: ['one', 'two'] };
      const query = new URL(createApiClient({ baseURL: 'http://localhost' }).getUri({ url: '/fixture', params: value }));
      expect(query.searchParams.get('profile[nested][title]')).toBe('fixture');
      expect(query.searchParams.getAll('labels[]')).toEqual(['one', 'two']);
      const form = new FormData();
      axios.toFormData(value, form);
      expect(form.get('profile[nested][title]')).toBe('fixture');
      expect(form.getAll('labels[]')).toEqual(['one', 'two']);
      expect(visited).toEqual([]);
    } finally {
      for (const name of ['visitor', 'maxDepth']) {
        if (Object.prototype.hasOwnProperty.call(descriptors, name)) Object.defineProperty(Object.prototype, name, descriptors[name]);
        else Reflect.deleteProperty(Object.prototype, name);
      }
    }
  });

  it('still honors explicitly supplied form options', () => {
    const visitor = vi.fn(function (this: FormData, value: unknown, key: string | number) { this.append(String(key), String(value).toUpperCase()); return false; });
    const form = new FormData();
    axios.toFormData({ title: 'fixture' }, form, { visitor });
    expect(form.get('title')).toBe('FIXTURE');
    expect(visitor).toHaveBeenCalledOnce();
    expect(() => axios.toFormData({ a: { b: { c: 'fixture' } } }, new FormData(), { maxDepth: 1 })).toThrow();
  });
});

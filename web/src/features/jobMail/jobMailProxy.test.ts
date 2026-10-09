import { describe, expect, it } from 'vitest';
import type { ProxyOptions, UserConfig } from 'vite';
import viteConfig from '../../../vite.config';

const rules = (viteConfig as UserConfig).server!.proxy!;
function matchRule(requestPath: string): ProxyOptions {
  const rule = Object.entries(rules).find(([pattern]) => pattern.startsWith('^')
    ? new RegExp(pattern).test(requestPath)
    : requestPath.startsWith(pattern));
  if (!rule || typeof rule[1] === 'string') throw new Error('Expected an explicit API proxy rule');
  return rule[1];
}

describe('job mail development same-origin boundary', () => {
  it('matches the dedicated mail rule before the generic API rule', () => {
    expect(Object.keys(rules)).toEqual(['^/api/job-mail(?:/|$)', '/api']);
    for (const path of ['/api/job-mail', '/api/job-mail/status', '/api/job-mail/imports', '/api/job-mail/suggestions/id/confirm']) {
      expect(matchRule(path)).toMatchObject({ target: 'http://localhost:8080', changeOrigin: false });
    }
  });
  it('does not widen host preservation to other or similarly named API routes', () => {
    for (const path of ['/api/applications', '/api/settings', '/api/job-mailbox', '/api/job-mail-export']) {
      expect(matchRule(path)).toMatchObject({ target: 'http://localhost:8080', changeOrigin: true });
    }
  });
  it('passes origin headers through without adding a bypass or rewriting mail paths', () => {
    const mail = matchRule('/api/job-mail/imports');
    expect(mail.headers).toBeUndefined();
    expect(mail.rewrite).toBeUndefined();
    expect(mail.configure).toBeUndefined();
  });
});

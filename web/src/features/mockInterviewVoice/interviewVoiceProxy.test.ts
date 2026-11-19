import { describe, expect, it } from 'vitest';
import type { UserConfig } from 'vite';
import config from '../../../vite.config';

const rules = Object.entries((config as UserConfig).server!.proxy!);
const matchRule = (path: string) => rules.find(([pattern]) => pattern.startsWith('^')
  ? new RegExp(pattern).test(path)
  : path.startsWith(pattern))?.[1];

describe('realtime interview development proxy', () => {
  it('preserves the dev host for the guarded capability without rewriting Origin', () => {
    for (const path of ['/api/interview-voice', '/api/interview-voice/sessions',
      '/api/interview-voice/sessions/id/offer', '/api/interview-voice/runtime/inspect']) {
      expect(matchRule(path)).toEqual({ target: 'http://localhost:8080', changeOrigin: false });
    }
  });

  it('leaves nearby and generic APIs unaffected', () => {
    for (const path of ['/api/interview-voices', '/api/interview-voice-shadow', '/api/chat']) {
      expect(matchRule(path)).toEqual({ target: 'http://localhost:8080', changeOrigin: true });
    }
  });
});

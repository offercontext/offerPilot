// Real production Haru component and production guard, with explicit geometry
// because jsdom has no layout engine. This creates no screenshots/Windows pass.
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { beforeEach, afterEach, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createMockScreenshots } from '../mock-screenshots.mjs';
import { fixtureFor } from '../mock-fixtures.cjs';
vi.mock('@/features/pilotMascot/live2dRuntime', () => ({ live2dPilotMascotRuntime: { mount: vi.fn() } }));
import DesktopHaruWindow from '../../../web/src/features/assistantSurface/DesktopHaruWindow';

const token = 'synthetic-haru-crop-guard-token';
const bodyText = fixtureFor('pilot-stream', { model: 'deepseek-flash', stream: true }).frames
  .filter((line: string) => !line.includes('[DONE]'))
  .map((line: string) => JSON.parse(line.slice(6)).choices[0]?.delta?.content || '').join('');
const box = (left: number, top: number, width: number, height: number) => ({
  left, top, right: left + width, bottom: top + height, width, height, x: left, y: top, toJSON() {},
});
let host: HTMLDivElement, root: ReturnType<typeof createRoot>, guard: (options: object) => Promise<string>;
let avatar: HTMLImageElement, frame: HTMLElement, paragraph: HTMLElement, portrait: HTMLImageElement, row: HTMLElement;
let theme: 'light' | 'dark', listener: (value: any) => void, state: any;
const check = (visual: boolean | string = 'stable') => guard({ screenId: `haru-session-${theme}`, stage: 'pilot-stream', tokens: [token], visual });
beforeEach(async () => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  theme = 'light';
  state = { connected: true, visible: true, expanded: true, generation: 1, alwaysOnTop: false,
    snapshot: { version: 1, conversationId: 7, theme, taskState: 'idle', loading: false,
      hasPending: false, canSend: true, canStop: false, stopping: false, error: '', stopMessage: '',
      contextLabel: '工作区', messages: [{ role: 'assistant', content: bodyText }] } };
  window.offerpilotDesktop = { role: 'haru', getState: async () => state,
    onState: fn => { listener = fn; return () => {}; }, windowAction: async () => true, request: vi.fn() } as any;
  window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(420);
  vi.spyOn(window, 'innerHeight', 'get').mockReturnValue(740);
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, 'getComputedStyle').mockImplementation(node => {
    const style = computed(node);
    return { display: style.display || 'block', visibility: style.visibility || 'visible', opacity: style.opacity || '1',
      filter: style.filter || 'none', overflowX: style.overflowX || 'visible', overflowY: style.overflowY || 'visible' } as CSSStyleDeclaration;
  });
  vi.spyOn(HTMLElement.prototype, 'getClientRects').mockImplementation(function (this: HTMLElement) {
    return this.isConnected ? [{}] as any : [] as any;
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue(box(0, 0, 420, 740));
  host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
  await act(async () => { root.render(<DesktopHaruWindow />); await new Promise(resolve => setTimeout(resolve, 0)); });
  paragraph = host.querySelector('article[data-role="assistant"] p')!;
  row = paragraph.closest('article')!;
  avatar = row.querySelector('.desktop-haru-message-avatar img')!;
  frame = avatar.parentElement!;
  portrait = host.querySelector('.desktop-haru-static-portrait')!;
  expect(paragraph.textContent).toBe(bodyText);
  expect(bodyText.length).toBeGreaterThan(250);
  const css = readFileSync(path.resolve(__dirname, '../../../web/src/features/assistantSurface/DesktopHaruWindow.css'), 'utf8');
  expect(css).toMatch(/\.desktop-haru-message-avatar \{[^}]*width: 27px; height: 27px;/);
  expect(css).toMatch(/\.desktop-haru-message-avatar img \{[^}]*transform: scale\(2\.15\)/);
  expect(css).toMatch(/article\[data-role=assistant\] \.desktop-haru-message-avatar \{ overflow: hidden;/);
  frame.style.overflowX = frame.style.overflowY = 'hidden';
  frame.getBoundingClientRect = () => box(20, 500, 27, 27);
  avatar.getBoundingClientRect = () => box(6.625, 498.7, 53.75, 53.75);
  paragraph.getBoundingClientRect = () => box(66, 500, 300, 600);
  portrait.getBoundingClientRect = () => box(7, 50, 407, 255);
  const messages = host.querySelector('.desktop-haru-messages') as HTMLElement;
  messages.style.overflowX = messages.style.overflowY = 'auto';
  messages.getBoundingClientRect = () => box(19, 390, 382, 242);
  for (const image of [avatar, portrait]) for (const [key, value] of Object.entries({ complete: true, naturalWidth: 1586, naturalHeight: 992 })) {
    Object.defineProperty(image, key, { configurable: true, value });
  }
  for (const node of document.querySelectorAll('*')) node.getAnimations = () => [];
  const collector = createMockScreenshots({ directory: path.join(os.tmpdir(), 'unused-haru-crop-guard'), mode: 'mock' });
  collector.registerToken(token);
  await collector.capture('haru-session-light', {
    async evaluate(fn: Function) { guard = window.eval(`(${fn.toString()})`); return 'HARU_SURFACE'; },
    async screenshot() { throw new Error('guard-only fixture cannot take screenshots'); },
  }, { stage: 'pilot-stream' });
});
afterEach(async () => {
  await act(async () => root.unmount()); host.remove(); delete window.offerpilotDesktop; vi.restoreAllMocks();
});

it.each(['light', 'dark'] as const)('accepts the real %s component with a long visible excerpt and intentionally cropped avatar', async value => {
  theme = value;
  state = { ...state, snapshot: { ...state.snapshot, theme } };
  await act(async () => listener(state));
  expect(avatar.alt).toBe('Haru 头像');
  expect(avatar.src).toContain(`haru-window-${theme}`);
  // This was permanently impossible under the old full-image width check.
  expect(frame.getBoundingClientRect().width).toBeLessThan(avatar.getBoundingClientRect().width);
  expect(await check()).toBe('PASSED');
});

it.each(['hidden-image', 'transparent-image', 'blurred-image', 'image-outside-crop', 'clipped-frame',
  'image-animation', 'ancestor-animation', 'body-offscreen', 'portrait-offscreen', 'unloaded-image', 'credential-control'])
('rejects %s despite the other visible Haru elements', async fault => {
  if (fault === 'hidden-image') avatar.style.display = 'none';
  if (fault === 'transparent-image') avatar.style.opacity = '0';
  if (fault === 'blurred-image') avatar.style.filter = 'blur(2px)';
  if (fault === 'image-outside-crop') avatar.getBoundingClientRect = () => box(300, 500, 53.75, 53.75);
  if (fault === 'clipped-frame') {
    frame.getBoundingClientRect = () => box(20, 620, 27, 27);
    avatar.getBoundingClientRect = () => box(6.625, 618.7, 53.75, 53.75);
  }
  if (fault === 'image-animation') avatar.getAnimations = () => [{ pending: false, playState: 'running' }] as any;
  if (fault === 'ancestor-animation') row.getAnimations = () => [{ pending: true, playState: 'paused' }] as any;
  if (fault === 'body-offscreen') paragraph.getBoundingClientRect = () => box(66, 800, 300, 600);
  if (fault === 'portrait-offscreen') portrait.getBoundingClientRect = () => box(500, 50, 407, 255);
  if (fault === 'unloaded-image') Object.defineProperty(avatar, 'naturalWidth', { value: 0 });
  if (fault === 'credential-control') host.querySelector('textarea')!.setAttribute('aria-label', 'API key');
  expect(await check()).not.toBe('PASSED');
});

// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';

const state = vi.hoisted(() => ({ patched: false, order: [] as string[], shaderSystem: class ShaderSystem {} }));
vi.mock('pixi.js', () => ({
  ShaderSystem: state.shaderSystem,
  Ticker: {},
  Application: class {
    stage = { addChild: vi.fn() };
    render = vi.fn();
    destroy = vi.fn();
    constructor() {
      expect(state.patched).toBe(true);
      state.order.push('application');
    }
  },
}));
vi.mock('@pixi/unsafe-eval', () => ({
  install: ({ ShaderSystem }: { ShaderSystem: unknown }) => {
    expect(ShaderSystem).toBe(state.shaderSystem);
    state.patched = true;
    state.order.push('install');
  },
}));
vi.mock('pixi-live2d-display/cubism4', () => ({ Live2DModel: {
  registerTicker: vi.fn(),
  from: vi.fn(async () => ({ width: 100, height: 200, anchor: { set: vi.fn() }, scale: { set: vi.fn() }, destroy: vi.fn() })),
} }));
import { live2dPilotMascotRuntime } from './live2dRuntime';

afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ''; });
it('patches the same Pixi module before renderer creation on initial mount and remount', async () => {
  vi.stubGlobal('Live2DCubismCore', {});
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  const host = document.createElement('div');
  const canvas = document.createElement('canvas');
  host.append(canvas);
  document.body.append(host);
  const first = await live2dPilotMascotRuntime.mount(canvas);
  first.dispose();
  state.patched = false;
  const second = await live2dPilotMascotRuntime.mount(canvas);
  second.dispose();
  expect(state.order).toEqual(['install', 'application', 'install', 'application']);
});

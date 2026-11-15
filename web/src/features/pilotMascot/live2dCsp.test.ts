// @vitest-environment node
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);

describe('official Pixi CSP adapter', () => {
  it('runs its real uniform sync with string code generation disabled', () => {
    const context = vm.createContext({ exports: {} }, { codeGeneration: { strings: false, wasm: false } });
    expect(() => vm.runInContext('new Function("return 1")', context)).toThrow();
    vm.runInContext(readFileSync(require.resolve('@pixi/unsafe-eval'), 'utf8'), context);
    const result = vm.runInContext(`
      class ShaderSystem {
        systemCheck() { new Function('return true'); }
        syncUniforms() { new Function('return true'); }
      }
      const calls = [];
      exports.install({ ShaderSystem });
      const shader = new ShaderSystem();
      shader.systemCheck();
      const values = { opacity: 0.75, position: [2, 3], tint: [1, 0.5, 0.25, 1],
        transform: [1,0,0,0,1,0,0,0,1], texture: { id: 'haru' } };
      const types = { opacity: 'float', position: 'vec2', tint: 'vec4', transform: 'mat3', texture: 'sampler2D' };
      const data = Object.fromEntries(Object.entries(types).map(([key,type]) => [key, {type,size:1,isArray:false}]));
      const cached = Object.fromEntries(Object.keys(types).map(key => [key, { location:key, value: key === 'opacity' || key === 'texture' ? -1 : [0,0,0,0] }]));
      shader.shader = { program: { uniformData:data } };
      shader.renderer = { gl: Object.fromEntries(['uniform1f','uniform2f','uniform4f','uniformMatrix3fv','uniform1i'].map(name => [name, (...args) => calls.push([name,...args])])),
        texture: { bind: (...args) => calls.push(['bind',...args]) } };
      shader.syncUniforms({uniforms: values}, {uniformData: cached});
      JSON.stringify(calls);
    `, context);
    expect(JSON.parse(result)).toEqual([
      ['uniform1f', 'opacity', 0.75],
      ['uniform2f', 'position', 2, 3],
      ['uniform4f', 'tint', 1, 0.5, 0.25, 1],
      ['uniformMatrix3fv', 'transform', false, [1,0,0,0,1,0,0,0,1]],
      ['bind', { id: 'haru' }, 0],
      ['uniform1i', 'texture', 0],
    ]);
  });
});

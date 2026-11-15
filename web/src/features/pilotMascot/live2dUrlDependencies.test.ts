import { createRequire } from 'node:module';
import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
// The trailing slash selects the npm browser shim, NOT Node's built-in url.
// Loading @pixi/utils through Node would select the built-in instead of the
// browser dependency used by Live2D's ModelSettings.resolveURL.
const url = require('url/') as Pick<typeof import('node:url'), 'parse' | 'format' | 'resolve'>;
interface QueryOptions {
  comma?: boolean;
  arrayLimit?: number;
  throwOnLimitExceeded?: boolean;
  plainObjects?: boolean;
  allowPrototypes?: boolean;
}
const qs = require('qs') as {
  parse: (input: string, options?: QueryOptions) => Record<string, unknown>;
  stringify: (input: Record<string, unknown>) => string;
};

describe('installed Live2D URL dependencies (no mocks)', () => {
  it('uses the npm URL shim and the same installed qs that the shim resolves', () => {
    expect(url).not.toBe(require('node:url'));
    expect(require.resolve('url/').replace(/\\/g, '/')).toMatch(/\/node_modules\/url\/url\.js$/);
    expect(createRequire(require.resolve('url/')).resolve('qs')).toBe(require.resolve('qs'));
  });

  it('parses local asset URLs with Unicode, repeated keys, spaces and fragments', () => {
    const parsed = url.parse('/live2d/haru/texture.png?label=%E6%98%A5&tag=a&tag=b&space=hello+world#face', true);
    expect(parsed.pathname).toBe('/live2d/haru/texture.png');
    expect(parsed.hash).toBe('#face');
    expect(parsed.query).toEqual({ label: '春', tag: ['a', 'b'], space: 'hello world' });
    expect(url.parse('/live2d/haru/texture.png', true).query).toEqual({});
  });

  it('formats and reparses query values without changing the asset path or fragment', () => {
    const query = { label: '春', tag: ['a', 'b'], space: 'hello world', empty: '' };
    const formatted = url.format({ pathname: '/live2d/haru/texture.png', query, hash: 'face' });
    expect(formatted).toBe('/live2d/haru/texture.png?label=%E6%98%A5&tag=a&tag=b&space=hello%20world&empty=#face');
    expect(url.parse(formatted, true).query).toEqual(query);
  });

  it.each([
    ['haru_greeter_t03.2048/texture_00.png', '/live2d/haru-receptionist/haru_greeter_t03.2048/texture_00.png'],
    ['../shared/texture%20one.png?scale=2#face', '/live2d/shared/texture%20one.png?scale=2#face'],
    ['/live2d/shared/texture.png', '/live2d/shared/texture.png'],
    ['?theme=dark', '/live2d/haru-receptionist/haru_greeter_t03.model3.json?theme=dark'],
    ['#pose', '/live2d/haru-receptionist/haru_greeter_t03.model3.json?rev=1#pose'],
  ])('resolves model-relative asset reference %s', (relative, expected) => {
    expect(url.resolve('/live2d/haru-receptionist/haru_greeter_t03.model3.json?rev=1', relative)).toBe(expected);
  });

  it('preserves nested and repeated query values through qs stringify/parse', () => {
    const value = { label: '春', tag: ['a', 'b'], empty: '', nested: { page: '2' } };
    expect(qs.parse(qs.stringify(value))).toEqual(value);
  });

  // GHSA-x5fp-wj9c-mxmx: four values only, never a resource-exhaustion payload.
  it.each(['a', 'a[]'])('enforces comma array limits for %s at the exact boundary', (key) => {
    const options = { comma: true, arrayLimit: 3, throwOnLimitExceeded: true };
    expect(qs.parse(`${key}=1,2,3`, options)).toEqual({ a: key === 'a' ? ['1', '2', '3'] : [['1', '2', '3']] });
    expect(() => qs.parse(`${key}=1,2,3,4`, options)).toThrow(RangeError);
  });

  // GHSA-4mjr-xmp4-gh2g: an ordinary query must not trigger a non-callable
  // constructor.isBuffer during a parse -> stringify round trip.
  it.each([{ plainObjects: true }, { allowPrototypes: true }])('round-trips an own constructor.isBuffer key with %j', (options) => {
    const query = 'x%5Bconstructor%5D%5BisBuffer%5D=y';
    const parsed = qs.parse(query, options);
    expect(parsed).toEqual({ x: { constructor: { isBuffer: 'y' } } });
    expect(qs.stringify(parsed)).toBe(query);
  });
});

import test from 'node:test';
import { createRequire } from 'node:module';
import * as asar from '@electron/asar';
import compatibility from '../../test-support/dependency-compatibility.cjs';
const asarRequire = createRequire(import.meta.resolve('@electron/asar'));

test('ASAR resolves a bounded minimatch optimizer', () => {
  compatibility.assertBoundedMinimatch(asarRequire('minimatch'));
});
test('ASAR and glob preserve archive contents, exclusions and unpack patterns', t => {
  return compatibility.assertAsarRoundTrip(t, asar);
});
test('ASAR and glob preserve directory links without crawling linked descendants', t => {
  return compatibility.assertAsarDirectoryLink(t, asar);
});

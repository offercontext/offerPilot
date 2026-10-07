import assert from 'node:assert/strict';
import { markUiStep } from './ui-locators.mjs';

// Re-query the Locator each time: React/Ant Form effects can replace an input
// after opening. Reading a hidden retained node once is not a readback check.
export async function waitForInputValue(input, expected, { timeoutMs = 20000, pollMs = 25, control = 'unspecified' } = {}) {
  markUiStep(input.page(), 'readback', control);
  await input.waitFor({ state: 'visible' });
  const deadline = Date.now() + timeoutMs;
  do {
    const actual = await input.inputValue();
    if (actual === expected) return actual;
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  } while (Date.now() <= deadline);
  const error = new Error('visible input did not settle to its expected UI value');
  error.name = 'TimeoutError';
  throw error;
}

export async function selectSegment(scope, name, { timeoutMs = 20000, pollMs = 25 } = {}) {
  const page = typeof scope.page === 'function' ? scope.page() : scope;
  markUiStep(page, 'selection-open', name);
  const radio = scope.getByRole('radio', { name, exact: true });
  assert.equal(await radio.count(), 1, 'one radio in the intended segmented control required');
  // Ant Segmented's input has zero size and pointer-events:none. The actual
  // label is the user-operated target and activates the native radio semantics.
  await radio.locator('xpath=ancestor::label[1]').click();
  markUiStep(page, 'selection-confirm', name);
  const deadline = Date.now() + timeoutMs;
  do {
    if (await radio.isChecked()) return;
    if (Date.now() >= deadline) break;
    await new Promise((resolve) => setTimeout(resolve, pollMs));
  } while (Date.now() <= deadline);
  assert.fail('segmented selection did not become checked');
}

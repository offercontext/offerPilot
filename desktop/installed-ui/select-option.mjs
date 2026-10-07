import assert from 'node:assert/strict';

// rc-select virtualizes role=option into a zero-size accessibility mirror. Click the
// actual rendered option instead, using real keyboard input to bring distant rows in.
export async function selectVisibleOption(page, input, label, maxMoves = 48) {
  assert.ok(typeof label === 'string' || label instanceof RegExp);
  await input.click();
  const popup = page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  await popup.waitFor({ state: 'visible' });
  assert.equal(await popup.count(), 1, 'one active Select popup required');
  const option = popup.locator('.ant-select-item-option').and(popup.getByTitle(label, { exact: typeof label === 'string' }));
  let found = false;
  for (let moves = 0; moves <= maxMoves; moves++) {
    const count = await option.count();
    assert.ok(count <= 1, 'option label is ambiguous');
    if (count === 1) { found = true; break; }
    if (moves < maxMoves) await input.press('ArrowDown');
  }
  assert.equal(found, true, 'requested option did not become visible through keyboard navigation');
  await option.click();
  await popup.waitFor({ state: 'hidden' });
  const selected = await input.evaluate((element) => element.closest('.ant-select')?.querySelector('.ant-select-selection-item')?.getAttribute('title'));
  if (typeof label === 'string') assert.equal(selected, label, 'selected visible label differs');
  else assert.match(selected || '', label, 'selected visible label differs');
}

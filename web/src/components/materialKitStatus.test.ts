import { describe, expect, it } from 'vitest';
import { getMaterialKitStatusForSave } from './materialKitStatus';

describe('getMaterialKitStatusForSave', () => {
  it('omits status for a persisted submitted kit even after the local selection changes', () => {
    expect(getMaterialKitStatusForSave('submitted', 'draft')).toBeUndefined();
    expect(getMaterialKitStatusForSave('submitted', 'ready')).toBeUndefined();
  });

  it('retains editable selections for persisted draft and ready kits', () => {
    expect(getMaterialKitStatusForSave('draft', 'ready')).toBe('ready');
    expect(getMaterialKitStatusForSave('ready', 'draft')).toBe('draft');
  });
});

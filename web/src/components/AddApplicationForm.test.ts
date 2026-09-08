import { describe, expect, it } from 'vitest';
import source from './AddApplicationForm.tsx?raw';

describe('AddApplicationForm source contract', () => {
  it('requires a close reason when a new application is created as closed', () => {
    expect(source).toContain("Form.useWatch('status', form)");
    expect(source).toContain("status === 'closed'");
    expect(source).toContain('closed_reason');
    expect(source).toContain('请输入关闭原因');
  });
});

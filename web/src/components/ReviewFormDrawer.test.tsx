import { describe, expect, it } from 'vitest';
import source from './ReviewFormDrawer.tsx?raw';

describe('ReviewFormDrawer event binding contract', () => {
  it('keeps event binding fields explicit and limited to interview events', () => {
    expect(source).toContain('application_event_id');
    expect(source).toContain("event.event_type === 'interview'");
    expect(source).toContain('listEvents');
  });

  it('does not send ownership fields during ordinary bound-note edits', () => {
    expect(source).toContain('application_id: undefined');
    expect(source).toContain('application_event_id: undefined');
    expect(source).toContain('application_event_id = input.application_event_id ?? null');
  });
});

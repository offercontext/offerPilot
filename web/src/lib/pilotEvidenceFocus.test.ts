import { describe, expect, it } from 'vitest';
import { eventFocusDate, findEvidenceFocusRecord } from './pilotEvidenceFocus';

describe('findEvidenceFocusRecord', () => {
  const records = [
    { id: 4, title: 'first' },
    { id: 9, title: 'second' },
  ];

  it('returns only the record with the exact focus id', () => {
    expect(findEvidenceFocusRecord(records, 9)).toEqual({ id: 9, title: 'second' });
  });

  it('returns undefined when the focus record is missing', () => {
    expect(findEvidenceFocusRecord(records, 5)).toBeUndefined();
  });

  it('returns undefined without a focus id', () => {
    expect(findEvidenceFocusRecord(records, undefined)).toBeUndefined();
  });
});

describe('eventFocusDate', () => {
  it('returns a calendar date for a valid timestamp', () => {
    expect(eventFocusDate('2026-07-11T09:30:00+08:00')).toBe('2026-07-11');
  });

  it('uses the calendar API UTC date at a local-midnight boundary', () => {
    expect(eventFocusDate('2026-07-31T23:30:00Z')).toBe('2026-07-31');
  });

  it('treats a timezone-less datetime as UTC', () => {
    expect(eventFocusDate('2026-07-11T00:30:00')).toBe('2026-07-11');
  });

  it('returns undefined for an invalid timestamp', () => {
    expect(eventFocusDate('not-a-timestamp')).toBeUndefined();
  });

  it('rejects date-only and impossible datetime values', () => {
    expect(eventFocusDate('2026-07-11')).toBeUndefined();
    expect(eventFocusDate('2026-02-30T09:30:00')).toBeUndefined();
  });
});

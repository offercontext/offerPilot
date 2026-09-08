import dayjs from 'dayjs';
import utc from 'dayjs/plugin/utc';

dayjs.extend(utc);

const EVENT_DATETIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/;

export function findEvidenceFocusRecord<T extends { id: number }>(
  records: readonly T[],
  focusId: number | undefined,
): T | undefined {
  if (focusId === undefined) return undefined;
  return records.find((record) => record.id === focusId);
}

export function eventFocusDate(scheduledAt: string): string | undefined {
  const match = EVENT_DATETIME.exec(scheduledAt);
  if (!match) return undefined;

  const [, year, month, day, hour, minute, second] = match;
  const calendarMonth = Number(month);
  const calendarDay = Number(day);
  if (
    calendarMonth < 1 ||
    calendarMonth > 12 ||
    calendarDay < 1 ||
    calendarDay > dayjs(`${year}-${month}-01`).daysInMonth() ||
    Number(hour) > 23 ||
    Number(minute) > 59 ||
    (second !== undefined && Number(second) > 59)
  ) {
    return undefined;
  }

  const date = dayjs.utc(scheduledAt);
  return date.isValid() ? date.format('YYYY-MM-DD') : undefined;
}

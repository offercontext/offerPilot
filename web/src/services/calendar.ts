import type { CalendarEntry } from '@/types/calendar';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 10000 });

// month param format: "YYYY-MM"
export async function getCalendar(month: string): Promise<CalendarEntry[]> {
  const { data } = await http.get<CalendarEntry[]>('/calendar', { params: { month } });
  return data;
}

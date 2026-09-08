import type { ScheduleEvent, ScheduleEventInput, ScheduleEventType } from '@/types/event';
import { createApiClient } from './http';

const http = createApiClient({
  baseURL: '/api',
  timeout: 10000,
});

interface ListEventsParams {
  month?: string;
  application_id?: number;
  event_type?: ScheduleEventType;
}

export async function listEvents(params?: ListEventsParams): Promise<ScheduleEvent[]> {
  const { data } = await http.get<ScheduleEvent[]>('/application-events', { params });
  return data;
}

export async function getEvent(id: number): Promise<ScheduleEvent> {
  const { data } = await http.get<ScheduleEvent>(`/application-events/${id}`);
  return data;
}

export async function createEvent(input: ScheduleEventInput): Promise<ScheduleEvent> {
  const { data } = await http.post<ScheduleEvent>('/application-events', input);
  return data;
}

export async function updateEvent(
  id: number,
  input: ScheduleEventInput
): Promise<ScheduleEvent> {
  const { data } = await http.put<ScheduleEvent>(`/application-events/${id}`, input);
  return data;
}

export async function deleteEvent(id: number): Promise<void> {
  await http.delete(`/application-events/${id}`);
}

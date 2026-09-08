import type {
  VoiceCoachingSnapshot,
  VoiceCoachingSnapshotCreate,
  VoiceCoachingTrends,
} from '@/types/voiceCoaching';
import { createApiClient } from './http';
import type { InterviewStudioContext } from './mockInterviews';

const http = createApiClient({ baseURL: '/api' });

function turnPath(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  turnNo: number;
}): string {
  return `/applications/${input.applicationId}/events/${input.eventId}/mock-interview/attempts/${input.attemptId}/turns/${input.turnNo}/voice-coaching-snapshot`;
}

function studioTurnPath(input: {
  context: InterviewStudioContext;
  attemptId: number;
  turnNo: number;
}): string {
  const prefix = input.context.kind === 'quick_practice'
    ? `/interview-practice-cases/${input.context.caseId}`
    : `/applications/${input.context.applicationId}/events/${input.context.eventId}`;
  return `${prefix}/mock-interview/attempts/${input.attemptId}/turns/${input.turnNo}/voice-coaching-snapshot`;
}

export async function saveVoiceCoachingSnapshot(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  turnNo: number;
  payload: VoiceCoachingSnapshotCreate;
}): Promise<VoiceCoachingSnapshot> {
  const { payload, ...pathInput } = input;
  const { data } = await http.post<VoiceCoachingSnapshot>(turnPath(pathInput), payload);
  return data;
}

export async function getVoiceCoachingSnapshot(input: {
  applicationId: number;
  eventId: number;
  attemptId: number;
  turnNo: number;
}): Promise<VoiceCoachingSnapshot> {
  const { data } = await http.get<VoiceCoachingSnapshot>(turnPath(input));
  return data;
}

export async function saveInterviewStudioVoiceCoachingSnapshot(input: {
  context: InterviewStudioContext;
  attemptId: number;
  turnNo: number;
  payload: VoiceCoachingSnapshotCreate;
}): Promise<VoiceCoachingSnapshot> {
  const { data } = await http.post<VoiceCoachingSnapshot>(studioTurnPath(input), input.payload);
  return data;
}

export async function getInterviewStudioVoiceCoachingSnapshot(input: {
  context: InterviewStudioContext;
  attemptId: number;
  turnNo: number;
}): Promise<VoiceCoachingSnapshot> {
  const { data } = await http.get<VoiceCoachingSnapshot>(studioTurnPath(input));
  return data;
}

export async function listVoiceCoachingSnapshots(input: {
  limit?: number;
  beforeId?: number;
} = {}): Promise<VoiceCoachingSnapshot[]> {
  const { data } = await http.get<{ items: VoiceCoachingSnapshot[] }>(
    '/interview/voice-coaching/snapshots',
    { params: { limit: input.limit ?? 20, before_id: input.beforeId } },
  );
  return data.items;
}

export async function getVoiceCoachingTrends(): Promise<VoiceCoachingTrends> {
  const { data } = await http.get<VoiceCoachingTrends>('/interview/voice-coaching/trends');
  return data;
}

export async function deleteVoiceCoachingSnapshot(snapshotId: number): Promise<void> {
  await http.delete(`/interview/voice-coaching/snapshots/${snapshotId}`);
}

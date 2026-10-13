import { createApiClient } from './http';
import type { InterviewStudioContext } from './mockInterviews';
import type { AnswerAcceptance } from '@/types/mockInterview';

const http = createApiClient({ baseURL: '/api/interview-voice', timeout: 25000 });
export interface VoiceGrant {
  session_id: string;
  attempt_id: number;
  epoch: number;
  token: string;
  consent_revision: string;
}
export interface VoiceCapabilities {
  available: boolean;
  reason: string;
  consent_revision: string;
  audio_processing: 'offerpilot_host';
  third_party_speech: false;
  stores_audio: false;
}
export async function voiceCapabilities(): Promise<VoiceCapabilities> {
  return (await http.get<VoiceCapabilities>('/capabilities')).data;
}
export async function createVoiceSession(input: {
  context: InterviewStudioContext; attemptId: number; turnNo: number;
  requestKey: string; consentRevision: string; takeover: boolean;
}): Promise<VoiceGrant> {
  return (await http.post<VoiceGrant>('/sessions', {
    context_kind: input.context.kind,
    ...(input.context.kind === 'quick_practice'
      ? { practice_case_id: input.context.caseId }
      : { application_id: input.context.applicationId, event_id: input.context.eventId }),
    attempt_id: input.attemptId, turn_no: input.turnNo,
    request_key: input.requestKey, consent: true,
    consent_revision: input.consentRevision, takeover: input.takeover,
  })).data;
}
export async function voiceSessionAction(grant: VoiceGrant, action: 'heartbeat' | 'close') {
  await http.post(`/sessions/${grant.session_id}/${action}`, { token: grant.token });
}
export async function voiceOffer(grant: VoiceGrant, sdp: string): Promise<RTCSessionDescriptionInit> {
  return (await http.post(`/sessions/${grant.session_id}/offer`, { token: grant.token, sdp, type: 'offer' })).data;
}
export function automaticAcceptance(grant: VoiceGrant): AnswerAcceptance {
  return {
    input_source: 'voice', acceptance_method: 'session_auto',
    voice_session_id: grant.session_id, voice_session_epoch: grant.epoch,
    voice_consent_revision: grant.consent_revision, voice_session_token: grant.token,
  };
}

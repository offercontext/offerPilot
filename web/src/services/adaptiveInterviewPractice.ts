import { t } from '@/i18n';
import axios from 'axios';
import type {
  AdaptivePracticeCompleteInput,
  AdaptivePracticePlan,
  AdaptivePracticeRecommendation,
  AdaptivePracticeV2StartInput,
} from '@/types/adaptiveInterviewPractice';
import { createApiClient } from './http';

const http = createApiClient({ baseURL: '/api', timeout: 15000 });

const COPY: Record<string, string> = {
  get adaptive_practice_not_found() { return t("练习或来源已不可见，请重新打开页面。"); },
  get adaptive_practice_source_conflict() { return t("复盘来源已变化，请重新核对后再开始。"); },
  get adaptive_practice_idempotency_conflict() { return t("本次操作内容已变化，请重新开始。"); },
  get adaptive_practice_revision_conflict() { return t("练习状态已变化，请重新加载。"); },
  get adaptive_practice_invalid_payload() { return t("练习内容不完整，请检查后重试。"); },
};

export class AdaptivePracticeError extends Error {
  constructor(public readonly code?: string, public readonly status?: number) {
    super(code && COPY[code] ? COPY[code] : t("复盘训练暂时不可用，请稍后重试。"));
    this.name = 'AdaptivePracticeError';
  }
}

function safeError(error: unknown): AdaptivePracticeError {
  const response = axios.isAxiosError(error)
    ? error.response
    : (error as { response?: { status?: unknown; data?: unknown } } | null)?.response;
  const data = response?.data as { error_code?: unknown } | undefined;
  const code = typeof data?.error_code === 'string' ? data.error_code : undefined;
  const status = typeof response?.status === 'number'
    && Number.isInteger(response.status)
    && response.status >= 100
    && response.status <= 599
    ? response.status
    : undefined;
  return new AdaptivePracticeError(code, status);
}

export async function listAdaptivePracticeRecommendations(): Promise<AdaptivePracticeRecommendation[]> {
  try {
    return (await http.get('/interview-practice/recommendations')).data;
  } catch (error) {
    throw safeError(error);
  }
}

export async function listAdaptivePracticePlans(): Promise<AdaptivePracticePlan[]> {
  try {
    return (await http.get('/interview-practice/plans')).data;
  } catch (error) {
    throw safeError(error);
  }
}

export async function startAdaptivePracticeV2(
  input: AdaptivePracticeV2StartInput,
): Promise<AdaptivePracticePlan> {
  try {
    return (await http.post('/interview-practice/plans', input)).data;
  } catch (error) {
    throw safeError(error);
  }
}

export async function completeAdaptivePractice(
  planId: number,
  input: AdaptivePracticeCompleteInput,
): Promise<AdaptivePracticePlan> {
  try {
    return (await http.post(`/interview-practice/plans/${planId}/complete`, input)).data;
  } catch (error) {
    throw safeError(error);
  }
}

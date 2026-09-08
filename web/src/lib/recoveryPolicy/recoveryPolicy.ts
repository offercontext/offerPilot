import {
  NETWORK_TRANSPORT_POLICY,
  RECOVERY_POLICIES,
  UNKNOWN_CODE_POLICY,
  type RecoveryDisposition,
  type RecoveryAttemptRetention,
  type RecoveryUserAction,
} from './generatedRecoveryPolicy';

export type RecoveryOperation = 'start' | 'answer' | 'question' | 'feedback';

export interface RecoveryDecision {
  errorCode: string | null;
  disposition: RecoveryDisposition;
  attemptRetention: RecoveryAttemptRetention;
  inputFrozen: boolean;
  preserveIdempotencyKey: boolean;
  providerRetryAllowed: boolean;
  userAction: RecoveryUserAction;
  attemptId: number | null;
  operationId: string | null;
  /** No further automatic recovery is possible; the user must act. */
  terminal: boolean;
  message: string;
}

const RETRY_SAME_KEY_MESSAGES: Record<RecoveryOperation, string> = {
  start: '第一题结果待确认，已保留原 question key。',
  answer: '回答提交结果待确认，已保留原回答 key。',
  question: '下一题结果待确认，已保留原 question key。',
  feedback: '复盘结果待确认，已保留原 feedback key。',
};

const DISPOSITION_MESSAGES: Record<RecoveryDisposition, string> = {
  retry_same_key: '结果待确认，已保留原 key。',
  restart_new_attempt: '该操作已无法用原 key 继续，请重新开始本次练习。',
  reload_source: '冻结来源暂时无法验证，请退出后回到准备中心重新确认。',
  edit_input: '当前回答或请求未通过校验，请修改后重试。',
  terminal_no_retry: '本次生成已终止，请重新开始练习。',
};

const CODE_MESSAGES: Partial<Record<string, string>> = {
  mock_interview_unverifiable: 'AI 输出未通过证据验证。本次生成已终止，请重新开始练习。',
  mock_interview_attempt_confirmed: '本次练习已有确认结果，不能删除或重复提交。',
  mock_interview_review_draft_already_confirmed: '本次复盘已确认，无需重复提交。',
  quick_practice_review_not_available: '快速练习暂不创建正式面试复盘。',
};

function messageFor(errorCode: string, disposition: RecoveryDisposition, operation: RecoveryOperation): string {
  if (disposition === 'retry_same_key') return RETRY_SAME_KEY_MESSAGES[operation];
  return CODE_MESSAGES[errorCode] ?? DISPOSITION_MESSAGES[disposition];
}

type RecoveryErrorData = {
  error_code?: string;
  attempt_id?: unknown;
  operation_id?: unknown;
  details?: { attempt_id?: unknown; operation_id?: unknown };
};

function responseOf(error: unknown): { status?: number; data?: RecoveryErrorData } | undefined {
  return (error as { response?: { status?: number; data?: RecoveryErrorData } } | null)?.response;
}

function attemptIdOf(data: { attempt_id?: unknown; details?: { attempt_id?: unknown } } | undefined): number | null {
  const raw = data?.attempt_id ?? data?.details?.attempt_id;
  return typeof raw === 'number' && Number.isInteger(raw) ? raw : null;
}

function operationIdOf(
  data: { operation_id?: unknown; details?: { operation_id?: unknown } } | undefined,
): string | null {
  const raw = data?.operation_id ?? data?.details?.operation_id;
  return typeof raw === 'string' && raw ? raw : null;
}

/**
 * Resolve a failed studio request strictly through the generated recovery
 * contract. HTTP status codes are never used to guess a recovery action:
 * a server response with an unknown (or missing) error code fails closed,
 * and only a transport failure without any response keeps the frozen key.
 */
export function resolveErrorRecovery(error: unknown, operation: RecoveryOperation): RecoveryDecision {
  const response = responseOf(error);
  const data = response?.data;
  const errorCode = typeof data?.error_code === 'string' && data.error_code ? data.error_code : null;
  if (!response) {
    return {
      errorCode: null,
      disposition: NETWORK_TRANSPORT_POLICY.disposition,
      attemptRetention: NETWORK_TRANSPORT_POLICY.attempt_retention,
      inputFrozen: NETWORK_TRANSPORT_POLICY.input_frozen,
      preserveIdempotencyKey: NETWORK_TRANSPORT_POLICY.preserve_idempotency_key,
      providerRetryAllowed: NETWORK_TRANSPORT_POLICY.provider_retry_allowed,
      userAction: NETWORK_TRANSPORT_POLICY.user_action,
      attemptId: null,
      operationId: null,
      terminal: false,
      message: `网络或服务结果待确认，${RETRY_SAME_KEY_MESSAGES[operation]}`,
    };
  }
  const entry = errorCode !== null ? RECOVERY_POLICIES[errorCode] : undefined;
  if (!entry) {
    return {
      errorCode,
      disposition: UNKNOWN_CODE_POLICY.disposition,
      attemptRetention: UNKNOWN_CODE_POLICY.attempt_retention,
      inputFrozen: UNKNOWN_CODE_POLICY.input_frozen,
      preserveIdempotencyKey: UNKNOWN_CODE_POLICY.preserve_idempotency_key,
      providerRetryAllowed: UNKNOWN_CODE_POLICY.provider_retry_allowed,
      userAction: UNKNOWN_CODE_POLICY.user_action,
      attemptId: attemptIdOf(data),
      operationId: operationIdOf(data),
      terminal: true,
      message: errorCode
        ? `遇到未登记的错误（${errorCode}），已停止自动恢复，请重新开始练习。`
        : '服务返回了未登记的错误，已停止自动恢复，请重新开始练习。',
    };
  }
  return {
    errorCode: entry.error_code,
    disposition: entry.disposition,
    attemptRetention: entry.attempt_retention,
    inputFrozen: entry.input_frozen,
    preserveIdempotencyKey: entry.preserve_idempotency_key,
    providerRetryAllowed: entry.provider_retry_allowed,
    userAction: entry.user_action,
    attemptId: attemptIdOf(data),
    operationId: operationIdOf(data),
    terminal: entry.disposition === 'terminal_no_retry' || entry.disposition === 'restart_new_attempt',
    message: messageFor(entry.error_code, entry.disposition, operation),
  };
}

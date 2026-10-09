export interface JobMailSecureCapability {
  available: boolean;
  reason: string;
  backend: string | null;
  local_only: true;
  credential_input_allowed: boolean;
  configured: boolean;
  deletion_pending: boolean;
}

export interface JobMailSetupSession {
  setup_token: string;
  expires_at: string;
}

export interface JobMailFolderChoice {
  id: string;
  name: string;
  selectable: boolean;
  excluded_by_default: boolean;
  special_use: string[];
}

export interface JobMailSetupTestResult extends JobMailSetupSession {
  email_masked: string;
  folders: JobMailFolderChoice[];
}

export class MailSecureSetupError extends Error {
  readonly code: string;
  constructor(code: string) {
    // Never retain the server response, request body, native error or its cause.
    super('安全邮箱配置操作未确认成功');
    this.name = 'MailSecureSetupError';
    this.code = code;
  }
}

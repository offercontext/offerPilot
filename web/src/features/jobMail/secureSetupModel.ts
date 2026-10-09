import { MailSecureSetupError, type JobMailSecureCapability } from '@/types/jobMailSecureSetup';

export function isLoopbackSetupPage(location: Pick<Location, 'hostname' | 'protocol'>): boolean {
  return ['http:', 'https:'].includes(location.protocol)
    && ['localhost', '127.0.0.1', '[::1]', '::1'].includes(location.hostname.toLowerCase());
}

export function canEnterMailCredential(capability: JobMailSecureCapability | undefined, location: Pick<Location, 'hostname' | 'protocol'>): boolean {
  return isLoopbackSetupPage(location) && capability?.available === true
    && capability.credential_input_allowed === true && capability.local_only === true
    && capability.configured === false && capability.deletion_pending === false;
}

export function mailSecureCapabilityReason(capability?: JobMailSecureCapability): string {
  if (!capability) return '尚未确认此部署的安全凭据能力，暂不接收授权码。可以继续手动粘贴邮件。';
  if (capability.deletion_pending) return '凭据删除尚未确认成功。请重试删除，系统不会把它显示为已清理。';
  if (capability.available && capability.credential_input_allowed) return capability.configured
    ? '授权码已配置在本机原生凭据库；页面不读取或回显授权码。'
    : '本机原生凭据库可用。你可以在受保护的本地页面亲自输入 QQ 邮箱授权码。';
  const reasons: Record<string, string> = {
    already_connected: '当前工作区已有邮箱连接，不会重复接收授权码。可以调整同步方式，或先明确断开现有连接再配置。',
    local_setup_not_enabled: '本机安全配置尚未开启。请由你在后端设备使用明确开启邮件安全配置的本地启动方式，仅绑定回环地址且不经反向代理；普通云端部署不接收授权码。',
    local_setup_in_use: '当前数据目录的邮件安全锁不可用，可能已有另一个后端在使用。请先检查并关闭重复实例；当前实例不会配置或恢复真实邮箱，也不会降级绕过锁。',
    keyring_unavailable: '当前后端未安装或无法使用原生凭据支持库。请由部署方补齐后重新检测。',
    keyring_missing: '当前后端未安装原生凭据支持库。请由部署方补齐后重新检测。',
    secure_store_unavailable: '本机原生凭据库不可用或未解锁，不会降级到明文配置。请检查系统凭据库及会话。',
    unsupported_backend: '当前凭据库不在受支持的原生系统库范围内，不会使用明文或文件存储替代。',
    remote_setup_denied: '本轮只支持直接打开后端设备的本地回环页面。远程部署的安全输入尚未验证，暂不接收授权码。',
    local_browser_required: '请在后端所在设备直接打开回环页面，并通过工作区登录校验。远程输入暂不开放。',
    local_setup_required: '请在后端所在设备打开本地回环页面。远程部署暂不开放授权码输入。',
    secure_setup_unavailable: '当前部署尚未满足原生凭据库及本地安全访问条件，请由部署方检查配置。',
  };
  return reasons[capability.reason] ?? '当前部署尚未通过安全凭据能力检查。请确认原生凭据支持库、系统会话和本地访问条件；不会接收授权码或降级保存。';
}

export function mailSecureSetupErrorText(error: unknown): string {
  const code = error instanceof MailSecureSetupError ? error.code : 'request_failed';
  const messages: Record<string, string> = {
    secure_setup_expired: '安全配置会话已过期。授权码不会自动重试，请重新开始。',
    secure_setup_conflict: '安全配置会话已失效或与当前状态冲突，请重新检测。',
    secure_input_invalid: '输入或确认项不完整。授权码已清空，请核实后重新开始。',
    secure_test_failed: 'QQ 登录或目录验证未成功。输入已清空，请核实邮箱、IMAP 设置和网络后重新开始。',
    secure_credential_write_failed: '凭据写入未确认成功。请刷新连接状态，不会自动重试保存。',
    credential_delete_pending: '系统凭据删除未成功，仍处于待删除状态。已停止检查；请重试删除，不能视为已清理。',
    local_browser_required: '仅允许后端设备上的本地回环页面配置或删除真实邮箱凭据。',
    setup_expired: '安全配置会话已过期。授权码不会自动重试，请重新开始。',
    setup_not_found: '安全配置会话已失效。请重新开始，不会自动重试授权码。',
    setup_replaced: '安全配置会话已失效或被替换，请重新开始。',
    mail_connection_failed: '未能验证 QQ 登录与目录。授权码输入已清空，请核实邮箱、IMAP 设置和网络后重新开始。',
    folder_discovery_failed: '目录验证失败，请重新开始。没有授权读取正文或自动同步。',
    secure_store_unavailable: '原生凭据库当前不可用。不会降级到明文保存，请重新检测本机能力。',
    credential_save_failed: '授权码保存未确认成功。请刷新凭据状态；不要重复提交或假定已保存。',
    credential_removal_failed: '凭据删除未成功。连接检查应保持停止；请重试删除。',
    invalid_request: '填写内容或确认项不完整。输入已清空，请核实后重新开始。',
    setup_not_local: '仅允许后端设备上的本地回环页面配置真实邮箱。',
    request_denied: '安全访问校验未通过，请核实本地页面和工作区登录状态。',
  };
  return messages[code] ?? '操作结果未确认。授权码输入已清空，不会自动重试。请刷新安全能力与连接状态后重新开始。';
}

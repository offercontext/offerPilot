import type { LogEntry, Settings } from '@/services/chat';

export function buildDiagnosticsText(settings: Settings, logs: LogEntry[]): string {
  const providers = settings.providers.map((provider) => {
    const roles = [
      provider.id === settings.active_provider_id ? 'active' : '',
      settings.fallback_provider_ids.includes(provider.id) ? 'fallback' : '',
    ].filter(Boolean).join(',');
    return `- ${provider.label} | ${provider.provider} | ${provider.model} | ${provider.enabled ? 'enabled' : 'disabled'}${roles ? ` | ${roles}` : ''}`;
  });
  return [
    `OfferPilot ${settings.version}`,
    `运行模式: ${settings.runtime_mode}`,
    `访问控制: ${settings.auth_enabled ? 'enabled' : 'disabled'}`,
    `日志级别: ${settings.log_level}`,
    `数据目录: ${settings.data_dir}`,
    '供应商:',
    ...providers,
    '最近日志:',
    ...logs.map((entry) => `${entry.level} ${entry.message}`),
  ].join('\n');
}

import { t } from '@/i18n';
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
    t("运行模式: {value0}", { value0: settings.runtime_mode }),
    t("访问控制: {value0}", { value0: settings.auth_enabled ? 'enabled' : 'disabled' }),
    t("日志级别: {value0}", { value0: settings.log_level }),
    t("数据目录: {value0}", { value0: settings.data_dir }),
    t("供应商:"),
    ...providers,
    t("最近日志:"),
    ...logs.map((entry) => `${entry.level} ${entry.message}`),
  ].join('\n');
}

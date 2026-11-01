import { liveText } from '@/i18n/liveText';
import { useLocalizedValidation } from './applicationUiText';
import { t, useLocale } from '@/i18n';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Button,
  Divider,
  Form,
  Input,
  InputNumber,
  List,
  Popconfirm,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  ArrowLeftOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  KeyOutlined,
  PlusOutlined,
  StarOutlined,
  SyncOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  getSettings,
  SETTINGS_QUERY_KEY,
  testProviderConnection,
  updateSettings,
  type AIProviderProfile,
  type ProviderConnectionTestResult,
  type Settings,
  type UpdateSettingsPayload,
} from '@/services/chat';
import { ONBOARDING_QUERY_KEY } from '@/services/onboarding';
import { testProviderImage } from '@/services/chatImages';
import workflowStyles from './ui/WorkflowSurface.module.css';

interface Props {
  open: boolean;
  onClose: () => void;
}

type EditableProvider = Omit<AIProviderProfile, 'has_api_key'> & {
  api_key?: string;
  has_api_key?: boolean;
};

interface FormValues {
  id: string;
  label: string;
  api_key?: string;
  provider: string;
  base_url: string;
  model: string;
  context_window: number;
  max_output_tokens: number;
  enabled: boolean;
  supports_json_schema: boolean;
  supports_vision: boolean;
  chat_auto_approve_writes: boolean;
}

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  get openai_compatible() { return t("OpenAI 兼容"); },
  anthropic: 'Anthropic',
  openrouter: 'OpenRouter',
};

const PROVIDER_OPTIONS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'openai_compatible', get label() { return t("OpenAI 兼容"); } },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'openrouter', label: 'OpenRouter' },
];

const DEFAULT_BASE_URL = 'https://api.openai.com/v1';
const DEFAULT_MODEL = 'gpt-4o';
const PROVIDER_FRAMING_RESERVE = 1_024;

export default function AISettingsDrawer({ open, onClose }: Props) {
  useLocale();
  const [form] = Form.useForm<FormValues>();
  useLocalizedValidation(form);
  const qc = useQueryClient();
  const initialized = useRef(false);
  const savedProviders = useRef<EditableProvider[]>([]);
  const testRevision = useRef(0);
  const nextProviderSequence = useRef(0);
  const [providers, setProviders] = useState<EditableProvider[]>([]);
  const [activeProviderId, setActiveProviderId] = useState('default');
  const [fallbackProviderIds, setFallbackProviderIds] = useState<string[]>([]);
  const [editingId, setEditingId] = useState('default');
  const [connectionResult, setConnectionResult] = useState<ProviderConnectionTestResult | null>(null);
  const [imageResult, setImageResult] = useState<ProviderConnectionTestResult | null>(null);

  useEffect(() => () => { testRevision.current += 1; }, []);

  const settingsQuery = useQuery({
    queryKey: SETTINGS_QUERY_KEY,
    queryFn: getSettings,
    enabled: open,
  });

  const editingProvider = providers.find((item) => item.id === editingId);
  const activeProvider = useMemo(
    () => providers.find((item) => item.id === activeProviderId) ?? providers[0],
    [activeProviderId, providers],
  );

  useEffect(() => {
    if (!open) {
      initialized.current = false;
      testRevision.current += 1;
      savedProviders.current = [];
      setProviders([]);
      form.resetFields();
      return;
    }
    if (initialized.current || !settingsQuery.data) return;
    initialized.current = true;
    const nextProviders = toEditableProviders(settingsQuery.data);
    savedProviders.current = nextProviders;
    const nextActiveId = settingsQuery.data.active_provider_id || nextProviders[0]?.id || 'default';
    setProviders(nextProviders);
    setActiveProviderId(nextActiveId);
    setFallbackProviderIds(settingsQuery.data.fallback_provider_ids ?? []);
    setEditingId(nextActiveId);
    setConnectionResult(null);
    setImageResult(null);
    form.setFieldsValue(providerToForm(nextProviders.find((item) => item.id === nextActiveId), settingsQuery.data));
  }, [form, open, settingsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      const values = await form.validateFields();
      const nextProviders = upsertProvider(providers, formToProvider(values, providers.find((item) => item.id === values.id)));
      const nextActiveId = nextProviders.some((item) => item.id === activeProviderId)
        ? activeProviderId
        : nextProviders[0]?.id || 'default';
      const nextFallbackIds = Array.from(
        new Set(
          fallbackProviderIds.filter(
            (providerId) => providerId !== nextActiveId && nextProviders.some((item) => item.id === providerId),
          ),
        ),
      );
      if (nextProviders.some((provider) => !provider.label.trim())) {
        throw new Error(t("请输入显示名称"));
      }
      const incompleteProvider = nextProviders.find(
        (provider) => providerRequiresValidBudget(provider, nextActiveId, nextFallbackIds)
          && !hasValidProviderBudget(provider),
      );
      if (incompleteProvider) {
        throw new Error(t("请先补全 {value0} 的模型预算配置", { value0: incompleteProvider.label || incompleteProvider.id }));
      }
      const nextActive = nextProviders.find((item) => item.id === nextActiveId) ?? nextProviders[0];
      setProviders(nextProviders);
      setActiveProviderId(nextActiveId);
      setFallbackProviderIds(nextFallbackIds);
      const payload: UpdateSettingsPayload = {
        chat_auto_approve_writes: values.chat_auto_approve_writes,
        active_provider_id: nextActiveId,
        fallback_provider_ids: nextFallbackIds,
        base_url: nextActive?.base_url || DEFAULT_BASE_URL,
        model: nextActive?.model || DEFAULT_MODEL,
        providers: nextProviders.map(providerPayload),
      };
      return updateSettings(payload);
    },
    onSuccess: (settings) => {
      qc.setQueryData(SETTINGS_QUERY_KEY, settings);
      qc.invalidateQueries({ queryKey: SETTINGS_QUERY_KEY });
      qc.invalidateQueries({ queryKey: ONBOARDING_QUERY_KEY });
      message.success(liveText(() => t("AI 设置已保存")));
      setProviders(toEditableProviders(settings));
      setActiveProviderId(settings.active_provider_id);
      setFallbackProviderIds(settings.fallback_provider_ids ?? []);
      form.setFieldValue('api_key', '');
      onClose();
    },
    onError: (error) => {
      message.error(error instanceof Error ? error.message : t("AI 设置保存失败"));
    },
  });

  const testMutation = useMutation({
    mutationFn: async () => {
      const revision = testRevision.current;
      try {
        const values = await form.validateFields();
        if (revision !== testRevision.current) return null;
        const existing = savedProviders.current.find((item) => item.id === values.id);
        const provider = formToProvider(values, existing);
        const payload =
          existing?.has_api_key && !provider.api_key && providerMatchesSavedFields(provider, existing)
            ? { provider_id: values.id }
            : { provider: providerPayload(provider) };
        return { result: await testProviderConnection(payload), revision };
      } catch {
        return { result: { ok: false, error: t("连接失败") }, revision };
      }
    },
    onSuccess: (response) => {
      if (!response || response.revision !== testRevision.current) return;
      const { result } = response;
      setConnectionResult(result);
      if (result.ok) {
        message.success(result.message || t("连接成功"));
      } else {
        message.error(result.error || t("连接失败"));
      }
    },
  });

  const hasKey = Boolean(activeProvider?.has_api_key);
  const imageTestMutation = useMutation({
    mutationFn: async () => {
      const revision = testRevision.current;
      try {
        const values = await form.validateFields();
        if (revision !== testRevision.current) return null;
        const existing = savedProviders.current.find((item) => item.id === values.id);
        const provider = formToProvider(values, existing);
        const result = await testProviderImage({ provider: providerPayload(provider) });
        return { result, revision };
      } catch {
        return { result: { ok: false, error: t("图片测试失败，请检查配置后重试。") }, revision };
      }
    },
    onSuccess: (response) => {
      if (response && response.revision === testRevision.current) setImageResult(response.result);
    },
  });
  const hasBudget = Boolean(activeProvider && hasValidProviderBudget(activeProvider));

  function startNewProvider() {
    let id: string;
    do { id = `provider-${++nextProviderSequence.current}`; } while (providers.some((item) => item.id === id));
    const provider: EditableProvider = {
      id,
      label: t("新供应商"),
      provider: 'openai',
      base_url: DEFAULT_BASE_URL,
      model: DEFAULT_MODEL,
      context_window: 0,
      max_output_tokens: 0,
      enabled: true,
      supports_json_schema: false,
      api_key: '',
      has_api_key: false,
    };
    setProviders((current) => [...current, provider]);
    editProvider(provider);
  }

  function editProvider(provider: EditableProvider) {
    const autoApprove = form.getFieldValue('chat_auto_approve_writes');
    testRevision.current += 1;
    setImageResult(null);
    setEditingId(provider.id);
    setConnectionResult(null);
    form.setFieldsValue({ ...providerToForm(provider, settingsQuery.data), chat_auto_approve_writes: autoApprove });
  }

  function retainProviderDraft(changed: Partial<FormValues>, values: FormValues) {
    setImageResult(null);
    if ('model' in changed || 'base_url' in changed || 'provider' in changed) {
      form.setFieldValue('supports_vision', false);
      values = { ...values, supports_vision: false };
    }
    testRevision.current += 1;
    setConnectionResult(null);
    const { chat_auto_approve_writes: _permission, ...draft } = values;
    setProviders((current) => upsertProvider(current, {
      ...draft,
      has_api_key: savedProviders.current.find((item) => item.id === editingId)?.has_api_key ?? false,
    }));
  }

  function removeProvider(providerId: string) {
    if (providerId === activeProviderId) return;
    const nextProviders = providers.filter((item) => item.id !== providerId);
    setProviders(nextProviders);
    setFallbackProviderIds((current) => current.filter((item) => item !== providerId));
    if (editingId === providerId) {
      const nextProvider = nextProviders.find((item) => item.id === activeProviderId) ?? nextProviders[0];
      if (nextProvider) {
        editProvider(nextProvider);
      }
    }
  }

  function markDefault(provider: EditableProvider) {
    if (!hasValidProviderBudget(provider)) {
      message.warning(liveText(() => t("请先补全该供应商的模型预算配置")));
      return;
    }
    setActiveProviderId(provider.id);
    setFallbackProviderIds((current) => current.filter((item) => item !== provider.id));
  }

  if (!open) return null;

  return (
    <section aria-label={t("AI 设置")} className={`${workflowStyles.surface} ${workflowStyles.stack}`} style={{ maxWidth: 1040 }}>
      <div className={workflowStyles.sectionHeader}>
        <div>
        <Button
          type="link"
          icon={<ArrowLeftOutlined />}
          onClick={onClose}
          disabled={saveMutation.isPending}
          style={{ width: 'fit-content', height: 'auto', padding: 0 }}
        >
          {t("返回设置")}</Button>
          <Typography.Title level={3} style={{ margin: '8px 0 0', color: 'var(--op-ink)' }}>
            <ApiOutlined /> {t("AI 设置")}</Typography.Title>
        </div>
          <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            {t("保存")}</Button>
      </div>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Alert
          type={hasKey && hasBudget ? 'success' : 'warning'}
          showIcon
          icon={hasKey && hasBudget ? <CheckCircleOutlined /> : <WarningOutlined />}
          message={!hasKey ? t("尚未配置密钥") : hasBudget ? t("AI 配置已就绪") : t("预算配置待补全")}
          description={
            !hasKey
              ? t("配置模型供应商密钥后即可使用 AI 能力。")
              : hasBudget
                ? t("密钥留空保存会保留当前密钥。")
                : t("请按当前模型的官方规格填写上下文窗口和单次最大输出。")
          }
        />

        <details>
          <summary>{t("多供应商管理（高级）")}</summary>
        <section data-testid="ai-provider-list" className={workflowStyles.section}>
          <Space align="center" style={{ width: '100%', justifyContent: 'space-between' }}>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {t("Provider 列表")}</Typography.Title>
            <Button icon={<PlusOutlined />} onClick={startNewProvider} disabled={saveMutation.isPending}>
              {t("新增供应商")}</Button>
          </Space>
          <List
            size="small"
            dataSource={providers}
            locale={{ emptyText: t("暂无供应商") }}
            renderItem={(provider) => (
              <List.Item
                className={workflowStyles.listRow}
                actions={[
                  <Button key="edit" size="small" icon={<EditOutlined />} onClick={() => editProvider(provider)} disabled={saveMutation.isPending}>
                    {t("编辑供应商")}</Button>,
                  <Button
                    key="default"
                    size="small"
                    icon={<StarOutlined />}
                    disabled={saveMutation.isPending || provider.id === activeProviderId || !hasValidProviderBudget(provider)}
                    onClick={() => markDefault(provider)}
                  >
                    {t("设为默认")}</Button>,
                  <Popconfirm
                    key="delete"
                    title={t("删除供应商")}
                    okText={t("删除")}
                    cancelText={t("取消")}
                    disabled={saveMutation.isPending || provider.id === activeProviderId}
                    onConfirm={() => removeProvider(provider.id)}
                  >
                    <Button size="small" danger icon={<DeleteOutlined />} disabled={saveMutation.isPending || provider.id === activeProviderId}>
                      {t("删除供应商")}</Button>
                  </Popconfirm>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space wrap>
                      <Typography.Text strong>{provider.label || provider.id}</Typography.Text>
                      {provider.id === activeProviderId ? <Tag color="blue">{t("默认")}</Tag> : null}
                      {fallbackProviderIds.includes(provider.id) ? <Tag color="gold">Fallback</Tag> : null}
                      {provider.has_api_key ? <Tag color="green">{t("密钥已配置")}</Tag> : <Tag>{t("未配置密钥")}</Tag>}
                      {provider.enabled && !hasValidProviderBudget(provider) ? (
                        <Tag color="orange">{t("预算配置待补全")}</Tag>
                      ) : null}
                    </Space>
                  }
                  description={`${PROVIDER_LABELS[provider.provider] || provider.provider} · ${provider.model}`}
                />
              </List.Item>
            )}
          />
        </section>
        </details>

        <Divider style={{ margin: 0 }} />

        <Form<FormValues>
          form={form}
          disabled={saveMutation.isPending}
          onValuesChange={retainProviderDraft}
          layout="vertical"
          initialValues={providerToForm(activeProvider, settingsQuery.data)}
          className={workflowStyles.section}
        >
          <Typography.Title level={5} style={{ marginTop: 0 }}>
            {t("编辑供应商")} · {editingProvider?.label || editingId}</Typography.Title>

          <Form.Item label={t("API 密钥")} name="api_key">
            <Input.Password
              prefix={<KeyOutlined />}
              autoComplete="off"
              placeholder={editingProvider?.has_api_key ? t("留空则保留当前密钥") : 'sk-...'}
            />
          </Form.Item>

          <Form.Item label={t("模型供应商")} name="provider">
            <Select options={PROVIDER_OPTIONS.map((option) => ({ ...option }))} />
          </Form.Item>

          <Form.Item label={t("接口地址")} name="base_url" tooltip={t("OpenAI 兼容接口地址")}>
            <Input placeholder={DEFAULT_BASE_URL} />
          </Form.Item>

          <Form.Item label={t("模型")} name="model">
            <Input placeholder={DEFAULT_MODEL} />
          </Form.Item>

          <Form.Item
            label={t("上下文窗口（tokens）")}
            name="context_window"
            required
            dependencies={['enabled', 'max_output_tokens']}
            tooltip={t("填写当前模型官方公布的上下文窗口，不会根据模型名称自动猜测。")}
            rules={[
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (getFieldValue('enabled') !== true && (!value || value <= 0)) return Promise.resolve();
                  if (!Number.isInteger(value) || value <= 0) {
                    return Promise.reject(new Error(t("请输入大于 0 的整数上下文窗口")));
                  }
                  const output = getFieldValue('max_output_tokens');
                  if (Number.isInteger(output) && output > 0 && value <= output + PROVIDER_FRAMING_RESERVE) {
                    return Promise.reject(new Error(t("上下文窗口必须大于最大输出与协议预留之和")));
                  }
                  return Promise.resolve();
                },
              }),
            ]}
          >
            <InputNumber min={1} precision={0} step={1_024} style={{ width: '100%' }} placeholder={t("例如 131072")} />
          </Form.Item>

          <Form.Item
            label={t("单次最大输出（tokens）")}
            name="max_output_tokens"
            required
            dependencies={['enabled', 'context_window']}
            tooltip={t("填写每次请求允许模型生成的最大 token 数。")}
            rules={[
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (getFieldValue('enabled') !== true && (!value || value <= 0)) return Promise.resolve();
                  if (!Number.isInteger(value) || value <= 0) {
                    return Promise.reject(new Error(t("请输入大于 0 的整数最大输出")));
                  }
                  const contextWindow = getFieldValue('context_window');
                  if (
                    Number.isInteger(contextWindow)
                    && contextWindow > 0
                    && contextWindow <= value + PROVIDER_FRAMING_RESERVE
                  ) {
                    return Promise.reject(new Error(t("单次最大输出必须为上下文和协议预留留出空间")));
                  }
                  return Promise.resolve();
                },
              }),
            ]}
          >
            <InputNumber min={1} precision={0} step={1_024} style={{ width: '100%' }} placeholder={t("例如 8192")} />
          </Form.Item>

          <details>
            <summary>{t("高级供应商选项")}</summary>
          <Form.Item label={t("配置 ID")} name="id" rules={[{ required: true, message: t("请输入配置 ID") }]}>
            <Input placeholder="openai" disabled />
          </Form.Item>

          <Form.Item label={t("显示名称")} name="label" rules={[{ required: true, message: t("请输入显示名称") }]}>
            <Input placeholder="OpenAI" />
          </Form.Item>

          <Form.Item label={t("启用")} name="enabled" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item
            label={t("原生 JSON Schema")}
            name="supports_json_schema"
            valuePropName="checked"
            tooltip={t("仅在 Provider 明确支持时启用")}
          >
            <Switch />
          </Form.Item>

          <Form.Item label={t("接受图片输入")} name="supports_vision" valuePropName="checked"
            tooltip={t("确认当前模型与接口支持原生图片输入后开启；修改模型或接口后需重新确认。")}>
            <Switch />
          </Form.Item>
          <Typography.Paragraph type="secondary">
            {t("图片直接发送给当前默认模型；包含图片时，只使用同样开启图片输入的备用模型。")}
          </Typography.Paragraph>
          <Form.Item label={t("Fallback 供应商")}>
            <Select
              mode="multiple"
              placeholder={t("默认供应商失败时依次尝试")}
              value={fallbackProviderIds}
              onChange={(value: string[]) => setFallbackProviderIds(value)}
              options={providers
                .filter((provider) => provider.id !== activeProviderId)
                .map((provider) => ({
                  value: provider.id,
                  label: provider.label || provider.id,
                  disabled: !hasValidProviderBudget(provider),
                }))}
            />
          </Form.Item>

          </details>

          <Divider />
          <Typography.Title level={5}>{t("权限与安全")}</Typography.Title>
          <Form.Item label={t("写操作自动确认")} name="chat_auto_approve_writes" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Space wrap className={workflowStyles.actionGroup}>
            <Button icon={<SyncOutlined />} loading={testMutation.isPending} onClick={() => testMutation.mutate()}>
              {t("测试连接")}
            </Button>
            <Button loading={imageTestMutation.isPending} onClick={() => imageTestMutation.mutate()}>{t("测试图片识别（会产生费用）")}</Button>
          </Space>
          {imageResult ? <Alert style={{ marginTop: 12 }} type={imageResult.ok ? 'success' : 'error'} showIcon
            message={imageResult.ok ? imageResult.message || t("图片识别通过") : imageResult.error || t("图片识别失败")} /> : null}

          {connectionResult ? (
            <Alert
              style={{ marginTop: 12 }}
              type={connectionResult.ok ? 'success' : 'error'}
              showIcon
              message={connectionResult.ok ? connectionResult.message || t("连接成功") : t(connectionResult.error || "连接失败")}
              description={
                connectionResult.ok && connectionResult.latency_ms !== undefined
                  ? `${connectionResult.model || ''} · ${connectionResult.latency_ms}ms`
                  : undefined
              }
            />
          ) : null}

          <Typography.Paragraph type="secondary" style={{ marginTop: 14, marginBottom: 0 }}>
            {t("测试连接不会保存。点击保存才会保存全部供应商草稿；返回或刷新会放弃未保存修改。密钥留空会保留已保存密钥。")}</Typography.Paragraph>
          <Typography.Paragraph type="secondary">
            {t("设置保存在本地 OfferPilot 配置中；接口只返回密钥是否存在。") }</Typography.Paragraph>

          <div style={{ marginTop: 14 }}>
            <Tag color="blue">LiteLLM</Tag>
            <Tag color="default">{t("本地配置")}</Tag>
          </div>
        </Form>
      </Space>
    </section>
  );
}

function toEditableProviders(settings?: Settings): EditableProvider[] {
  const providers = settings?.providers?.length
    ? settings.providers
    : [
        {
          id: 'default',
          label: 'Default',
          provider: 'openai',
          base_url: DEFAULT_BASE_URL,
          model: DEFAULT_MODEL,
          context_window: 0,
          max_output_tokens: 0,
          enabled: true,
          supports_json_schema: false,
          has_api_key: false,
        },
      ];
  return providers.map((provider) => ({ ...provider, api_key: '' }));
}

function providerToForm(provider?: EditableProvider, settings?: Settings): FormValues {
  return {
    id: provider?.id || 'default',
    label: provider?.label ?? 'Default',
    api_key: provider?.api_key ?? '',
    provider: provider?.provider || 'openai',
    base_url: provider?.base_url ?? settings?.base_url ?? DEFAULT_BASE_URL,
    model: provider?.model ?? settings?.model ?? DEFAULT_MODEL,
    context_window: provider?.context_window ?? 0,
    max_output_tokens: provider?.max_output_tokens ?? 0,
    enabled: provider?.enabled ?? true,
    supports_json_schema: provider?.supports_json_schema ?? false,
    supports_vision: provider?.supports_vision ?? false,
    chat_auto_approve_writes: settings?.chat_auto_approve_writes ?? false,
  };
}

function formToProvider(values: FormValues, existing?: EditableProvider): EditableProvider {
  return {
    id: values.id.trim(),
    label: values.label.trim() || values.id.trim(),
    provider: values.provider,
    api_key: values.api_key?.trim() || '',
    base_url: values.base_url.trim() || DEFAULT_BASE_URL,
    model: values.model.trim() || DEFAULT_MODEL,
    context_window: values.context_window,
    max_output_tokens: values.max_output_tokens,
    enabled: values.enabled,
    supports_json_schema: values.supports_json_schema,
    supports_vision: values.supports_vision,
    has_api_key: Boolean(existing?.has_api_key),
  };
}

function upsertProvider(providers: EditableProvider[], provider: EditableProvider): EditableProvider[] {
  const index = providers.findIndex((item) => item.id === provider.id);
  if (index === -1) return [...providers, provider];
  return providers.map((item) => (item.id === provider.id ? provider : item));
}

function providerPayload(provider: EditableProvider): Omit<AIProviderProfile, 'has_api_key'> & { api_key?: string } {
  const payload: Omit<AIProviderProfile, 'has_api_key'> & { api_key?: string } = {
    id: provider.id,
    label: provider.label.trim() || provider.id,
    provider: provider.provider,
    base_url: provider.base_url.trim() || DEFAULT_BASE_URL,
    model: provider.model.trim() || DEFAULT_MODEL,
    context_window: provider.context_window,
    max_output_tokens: provider.max_output_tokens,
    enabled: provider.enabled,
    supports_json_schema: provider.supports_json_schema,
    supports_vision: provider.supports_vision ?? false,
  };
  if (provider.api_key?.trim()) {
    payload.api_key = provider.api_key.trim();
  }
  return payload;
}

function providerMatchesSavedFields(provider: EditableProvider, saved: EditableProvider) {
  return (
    provider.provider === saved.provider &&
    provider.base_url === saved.base_url &&
    provider.model === saved.model &&
    Boolean(provider.supports_vision) === Boolean(saved.supports_vision) &&
    provider.context_window === saved.context_window &&
    provider.max_output_tokens === saved.max_output_tokens &&
    provider.enabled === saved.enabled &&
    provider.supports_json_schema === saved.supports_json_schema
  );
}

function hasValidProviderBudget(provider: Pick<AIProviderProfile, 'context_window' | 'max_output_tokens'>) {
  return (
    Number.isInteger(provider.context_window)
    && Number.isInteger(provider.max_output_tokens)
    && provider.context_window > provider.max_output_tokens + PROVIDER_FRAMING_RESERVE
    && provider.max_output_tokens > 0
  );
}

function providerRequiresValidBudget(
  provider: Pick<AIProviderProfile, 'id' | 'enabled'>,
  activeProviderId: string,
  fallbackProviderIds: string[],
) {
  return provider.enabled || provider.id === activeProviderId || fallbackProviderIds.includes(provider.id);
}

import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Modal, Input, Form, Select, Space, Typography, message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { checkApplicationDuplicates, createApplicationWithJd, getApplicationCreationScope } from '@/services/applications';
import { claimPendingCreation, clearPendingCreation, loadPendingCreations, savePendingCreation } from '@/services/applicationCreationRecovery';
import type { PendingCreation } from '@/services/applicationCreationRecovery';
import { ONBOARDING_QUERY_KEY } from '@/services/onboarding';
import { STATUS_LABELS } from '@/types/application';
import type { Application, ApplicationCreationInput, ApplicationDuplicates, ApplicationStatus } from '@/types/application';

interface AddApplicationFormProps {
  open: boolean;
  onClose: () => void;
  onCreated?: (application: Application) => void;
}
const STATUS_OPTIONS = (Object.entries(STATUS_LABELS) as [ApplicationStatus, string][]).map(
  ([value, label]) => ({ value, label: value === 'pending' ? '准备投递' : value === 'applied' ? '补录已投递' : label }),
);
export default function AddApplicationForm({ open, onClose, onCreated }: AddApplicationFormProps) {
  const queryClient = useQueryClient();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const generation = useRef(0);
  const openRef = useRef(open);
  openRef.current = open;
  const [scope, setScope] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingCreation | null>(null);
  const [review, setReview] = useState<ApplicationCreationInput | null>(null);
  const [duplicates, setDuplicates] = useState<ApplicationDuplicates | null>(null);
  const [error, setError] = useState('');
  const [checkFailed, setCheckFailed] = useState(false);
  const [override, setOverride] = useState(false);
  const bypassed = useRef(new Set<string>());
  const status = Form.useWatch('status', form);
  const jdMode = Form.useWatch('jd_mode', form);

  async function initialize() {
    const currentGeneration = ++generation.current;
    setError(''); setScope(null);
    try {
      const identity = await getApplicationCreationScope();
      if (generation.current !== currentGeneration || !openRef.current) return;
      const records = loadPendingCreations(identity);
      setScope(identity);
      setPending(records.find((record) => !bypassed.current.has(record.request.idempotency_key)) ?? null);
    } catch {
      if (generation.current === currentGeneration && openRef.current) setError('无法读取工作区或本地恢复记录，请重试；暂未提交。');
    }
  }
  useEffect(() => { if (open) void initialize(); }, [open]);

  const close = () => {
    generation.current++;
    form.resetFields(); setReview(null); setDuplicates(null); setOverride(false); onClose();
  };
  const cacheApplication = (application: Application) => {
    queryClient.setQueryData<Application[]>(['applications'], (previous) =>
      previous?.some((item) => item.id === application.id) ? previous : [application, ...(previous ?? [])],
    );
  };
  async function prepare() {
    if (busyRef.current || !scope) return;
    busyRef.current = true; setBusy(true); setError('');
    const currentGeneration = generation.current;
    try {
      const values = await form.validateFields();
      if (generation.current !== currentGeneration || !openRef.current) return;
      const unresolved = loadPendingCreations(scope).find((r) => !bypassed.current.has(r.request.idempotency_key));
      if (unresolved) { setPending(unresolved); return; }
      const request: ApplicationCreationInput = {
        company_name: values.company_name, position_name: values.position_name,
        job_url: values.job_url ?? '', status: values.status ?? 'pending',
        notes: values.notes ?? '', closed_reason: values.closed_reason ?? '',
        idempotency_key: crypto.randomUUID(),
        expected_scope_id: scope,
        initial_jd: values.jd_mode === 'now'
          ? { jd_text: values.jd_text, source_url: values.source_url?.trim() || null } : null,
      };
      setReview(request); setDuplicates(null); setCheckFailed(false);
      try {
        const checked = await checkApplicationDuplicates(request);
        if (generation.current === currentGeneration && openRef.current) setDuplicates(checked);
      } catch {
        if (generation.current === currentGeneration && openRef.current) setCheckFailed(true);
      }
    } catch (err) {
      if (!(err && typeof err === 'object' && 'errorFields' in err)) setError('无法读取恢复记录，暂未提交。');
    } finally { busyRef.current = false; setBusy(false); }
  }
  async function submit(request: ApplicationCreationInput) {
    if (busyRef.current || !scope) return;
    busyRef.current = true; setBusy(true); setError('');
    const currentGeneration = generation.current;
    const attempt: PendingCreation = { request, status: 'submitting' };
    try {
      const unresolved = await claimPendingCreation(scope, attempt, bypassed.current);
      if (unresolved) {
        setPending(unresolved); setReview(null);
        busyRef.current = false; setBusy(false); return;
      }
    }
    catch {
      setError('无法保存本地恢复记录，暂未发送请求。请检查浏览器存储后重试。');
      busyRef.current = false; setBusy(false); return;
    }
    setPending(attempt);
    let result: Application;
    try { result = await createApplicationWithJd(request); }
    catch (err) {
      const statusCode = isAxiosError(err) ? err.response?.status : undefined;
      if (statusCode === 400 || statusCode === 422) {
        try { clearPendingCreation(scope, request.idempotency_key); setPending(null); }
        catch { /* Preserve recovery if cleanup fails. */ }
        setReview(request);
        setError('保存校验失败，未创建记录。请返回修改后重新核对。');
      } else {
        const unknown: PendingCreation = { request, status: 'unknown' };
        setPending(unknown);
        try { savePendingCreation(scope, unknown); } catch { /* Original record remains. */ }
        setError(statusCode === 409 ? '原请求内容发生冲突，请检查已有记录；不能换键当作恢复。' : '创建结果未知，请使用原请求查询／恢复结果。');
      }
      busyRef.current = false; setBusy(false); return;
    }
    try { clearPendingCreation(scope, request.idempotency_key); }
    catch { message.warning('已保存，但本地回执未清理；再次恢复会返回同一记录。'); }
    setPending(null); setReview(null);
    cacheApplication(result);
    void queryClient.invalidateQueries({ queryKey: ['applications'] });
    void queryClient.invalidateQueries({ queryKey: ONBOARDING_QUERY_KEY });
    busyRef.current = false; setBusy(false);
    message.success('已保存投递');
    if (generation.current === currentGeneration && openRef.current) {
      close(); onCreated?.(result);
    }
  }
  return (
    <Modal title="添加投递" open={open} width={640} onCancel={close} footer={null}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {error && <Alert type="error" showIcon message={error} />}
        {!scope && <Button onClick={() => void initialize()}>重试读取工作区</Button>}
        {pending ? <>
          <Alert type="warning" showIcon message={busy ? '正在确认创建结果' : '有一条待恢复提交'} description="关闭窗口不会撤销已发送的请求。恢复会使用原内容和原请求标识。" />
          <Typography.Text strong>{pending.request.company_name} · {pending.request.position_name}</Typography.Text>
          <Typography.Text>状态：{STATUS_LABELS[pending.request.status ?? 'pending']}</Typography.Text>
          <Button type="primary" loading={busy} onClick={() => void submit(pending.request)}>查询／恢复结果</Button>
          <Checkbox checked={override} disabled={busy} onChange={(e) => setOverride(e.target.checked)}>无法恢复且仍需另建：我理解旧提交可能已成功，另建可能产生重复记录</Checkbox>
          {override && <Button danger disabled={busy} onClick={() => {
            bypassed.current.add(pending.request.idempotency_key);
            setPending(null); setReview(null); setOverride(false); form.resetFields();
          }}>承担重复风险，另建新草稿</Button>}
        </> : review ? <>
          <Typography.Title level={5}>核对后确认保存</Typography.Title>
          <Typography.Text strong>{review.company_name} · {review.position_name}</Typography.Text>
          <Typography.Text>状态：{STATUS_LABELS[review.status ?? 'pending']}</Typography.Text>
          <Typography.Text>岗位链接：{review.job_url || '未填写'}</Typography.Text>
          <Typography.Text>本版 JD 来源：{review.initial_jd?.source_url || '未填写'}</Typography.Text>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflow: 'auto' }}>{review.initial_jd?.jd_text ?? '稍后补充 JD'}</pre>
          <Typography.Text>备注：{review.notes || '无'}</Typography.Text>
          {review.status === 'closed' && <Typography.Text>关闭原因：{review.closed_reason}</Typography.Text>}
          {checkFailed ? <Alert type="warning" message="未能完成检查" description="可以返回重试检查，或明确选择仍然创建。" />
            : duplicates && <Alert type={duplicates.items.length ? 'warning' : 'info'} message={duplicates.items.length ? '发现可能重复的投递' : '未发现符合规则的重复记录'} />}
          {duplicates?.items.map((item) => <div key={item.id}>
            <Typography.Text>{item.company_name} · {item.position_name} · {STATUS_LABELS[item.status]}</Typography.Text>
            <div style={{ overflowWrap: 'anywhere' }}>{item.job_url || '未填写岗位链接'}</div>
            <Button onClick={() => { cacheApplication(item); close(); onCreated?.(item); }}>打开已有记录</Button>
          </div>)}
          {duplicates?.has_more && <Typography.Text>还有更多可能重复记录，仅展示前 20 条。</Typography.Text>}
          <Space><Button disabled={busy} onClick={() => {
            form.setFieldsValue({ ...review, jd_mode: review.initial_jd ? 'now' : 'later',
              jd_text: review.initial_jd?.jd_text, source_url: review.initial_jd?.source_url ?? '' });
            setReview(null);
          }}>返回修改</Button>
            <Button type="primary" loading={busy} disabled={!scope || (!duplicates && !checkFailed)} onClick={() => void submit(review)}>{checkFailed || duplicates?.items.length ? '仍然创建' : '确认保存'}</Button>
          </Space>
        </> : <Form form={form} layout="vertical" initialValues={{ status: 'pending', jd_mode: 'later' }}>
          <Form.Item name="company_name" label="公司" rules={[{ required: true, whitespace: true, message: '请输入公司名称' }]}><Input /></Form.Item>
          <Form.Item name="position_name" label="岗位" rules={[{ required: true, whitespace: true, message: '请输入岗位名称' }]}><Input /></Form.Item>
          <Form.Item name="status" label="录入意图／状态"><Select options={STATUS_OPTIONS} /></Form.Item>
          {status === 'closed' && <Form.Item name="closed_reason" label="关闭原因" rules={[{ required: true, whitespace: true, message: '请输入关闭原因' }]}><Input.TextArea rows={2} /></Form.Item>}
          <Form.Item name="job_url" label="岗位链接"><Input placeholder="仅保存，不访问来源网址" /></Form.Item>
          <Form.Item name="jd_mode" label="JD 原文"><Select options={[{ value: 'later', label: '稍后补充 JD' }, { value: 'now', label: '现在填写／粘贴 JD' }]} /></Form.Item>
          {jdMode === 'now' && <>
            <Form.Item name="jd_text" label="本版 JD 原文" rules={[{ required: true, whitespace: true, message: '请粘贴 JD 原文，或选择稍后补充' }]}><Input.TextArea rows={6} /></Form.Item>
            <Form.Item name="source_url" label="本版 JD 来源（可选）"><Input placeholder="https://..." /></Form.Item>
            <Button onClick={() => form.setFieldValue('source_url', form.getFieldValue('job_url') ?? '')}>使用岗位链接作为本版来源</Button>
          </>}
          <Form.Item name="notes" label="备注"><Input.TextArea rows={2} /></Form.Item>
          <Space><Button onClick={close}>取消</Button><Button type="primary" disabled={!scope} loading={busy} onClick={() => void prepare()}>核对并检查重复</Button></Space>
        </Form>}
        <Typography.Text type="secondary">本地恢复记录被清除或换设备后，请先检查已有投递。</Typography.Text>
      </Space>
    </Modal>
  );
}

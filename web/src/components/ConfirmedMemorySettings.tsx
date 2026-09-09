import { Alert, Button, Input, Modal, Space, Tag, Typography } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { changeConfirmedMemory, getConfirmedMemory, listConfirmedMemory, type ConfirmedMemory, type MemoryMutation } from '@/services/confirmedMemory';

export default function ConfirmedMemorySettings() {
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['confirmed-memory'], queryFn: listConfirmedMemory });
  const [editing, setEditing] = useState<ConfirmedMemory | 'new'>();
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [detail, setDetail] = useState<ConfirmedMemory>();
  const [notice, setNotice] = useState('');
  const detailGeneration = useRef(0);
  useEffect(() => () => { detailGeneration.current += 1; }, []);
  async function readVersions(id: string) {
    const generation = ++detailGeneration.current;
    try { const value = await getConfirmedMemory(id); if (generation === detailGeneration.current) setDetail(value); }
    catch { if (generation === detailGeneration.current) setError('版本记录读取失败'); }
  }
  // Preserve the original mutation through a lost response. Editing the payload
  // intentionally creates a new mutation and still uses the original CAS version.
  const retry = useRef<{ key: string; command: MemoryMutation }>();
  async function apply(item: ConfirmedMemory | undefined, action: MemoryMutation['action'], value = '') {
    const key = JSON.stringify([item?.id, item?.current_version ?? 0, action, value]);
    const command = retry.current?.key === key ? retry.current.command : {
      mutation_id: crypto.randomUUID(), action, expected_version: item?.current_version ?? 0,
      confirmed: true as const, content: value,
    };
    detailGeneration.current += 1;
    retry.current = { key, command }; setBusy(true); setError('');
    try {
      await changeConfirmedMemory(item?.id, command);
      retry.current = undefined; setEditing(undefined); setDetail(undefined);
      setNotice(action === 'delete' ? '偏好及其历史正文已删除' : action === 'withdraw' ? '偏好已撤回，下次回复不再使用' : '偏好已确认保存');
      await cache.invalidateQueries({ queryKey: ['confirmed-memory'] });
    } catch {
      setError('操作未确认。可重试原操作；若内容已变化，请关闭编辑并刷新后重新确认。');
      throw new Error('memory_mutation_unconfirmed');
    } finally { setBusy(false); }
  }
  return <section aria-labelledby="confirmed-memory-title" style={{ background: 'var(--op-surface)', border: '1px solid var(--op-border)', borderRadius: 16, padding: 24, display: 'grid', gap: 16 }}>
    <div>
      <Typography.Title id="confirmed-memory-title" level={4} style={{ margin: 0 }}>已确认的个人偏好</Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>只保存你明确确认的表达和求职偏好。当前请求优先；偏好不会授权 AI 执行操作。</Typography.Paragraph>
    </div>
    {notice && <div role="status">{notice}</div>}
    {(query.isError || error) && <Alert type="error" showIcon message={error || '偏好读取失败，请重试'} action={<Button onClick={() => { setError(''); void query.refetch(); }}>刷新</Button>} />}
    {query.isLoading ? <div role="status">正在读取偏好…</div> : !query.data?.length && <Typography.Text type="secondary">还没有已确认的偏好。</Typography.Text>}
    {query.data?.map((item) => <div key={item.id} style={{ display: 'grid', gap: 8, paddingBlock: 12, borderBottom: '1px solid var(--op-border)' }}>
      <div style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.content}</div>
      <Space wrap>
        <Tag>{item.state === 'active' ? '已确认' : '已撤回'}</Tag>
        <Button disabled={busy} onClick={() => { retry.current = undefined; setEditing(item); setContent(item.content); setError(''); }}>{item.state === 'active' ? '编辑' : '重新确认'}</Button>
        <Button disabled={busy} onClick={() => void readVersions(item.id)}>查看版本</Button>
        {item.state === 'active' && <Button disabled={busy} onClick={() => Modal.confirm({ title: '撤回这项偏好？', content: '撤回后不再用于后续回复，历史版本保留。', okText: '确认撤回', cancelText: '取消', onOk: () => apply(item, 'withdraw') })}>撤回</Button>}
        <Button danger disabled={busy} onClick={() => Modal.confirm({ title: '删除这项偏好？', content: '当前内容及所有历史版本正文都会删除，无法恢复。', okText: '确认删除', cancelText: '取消', onOk: () => apply(item, 'delete') })}>删除</Button>
      </Space>
    </div>)}
    <div><Button disabled={busy} onClick={() => { retry.current = undefined; setEditing('new'); setContent(''); setError(''); }}>添加偏好</Button></div>
    <Modal open={editing !== undefined} title={editing === 'new' ? '确认个人偏好' : '编辑并重新确认偏好'} okText="确认保存" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !content.trim() }} onCancel={() => { if (!busy) { retry.current = undefined; setEditing(undefined); void query.refetch(); } }} onOk={() => void apply(editing === 'new' ? undefined : editing, 'confirm', content.trim()).catch(() => undefined)}>
      <label htmlFor="confirmed-memory-content">你希望 AI 记住的偏好</label>
      <Input.TextArea id="confirmed-memory-content" value={content} onChange={(event) => setContent(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 4, maxRows: 10 }} disabled={busy} />
      {error && <Alert type="error" message={error} style={{ marginTop: 24 }} />}
    </Modal>
    <Modal open={!!detail} title="已确认的版本记录" footer={<Button onClick={() => { detailGeneration.current += 1; setDetail(undefined); }}>关闭</Button>} onCancel={() => { detailGeneration.current += 1; setDetail(undefined); }}>
      {detail?.versions?.map((version) => <div key={version.version} style={{ marginBlock: 16 }}><Typography.Text type="secondary">版本 {version.version} · {new Date(version.confirmed_at).toLocaleString()}</Typography.Text><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{version.content}</p></div>)}
    </Modal>
  </section>;
}

import { Alert, Button, Modal, Space, Tag } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { generateOlderSummary, withdrawOlderSummary, type OlderSummary } from '@/services/conversationSummary';

export default function ConversationSummaryControl({ conversationId, busy }: { conversationId?: number; busy: boolean }) {
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState<OlderSummary>();
  const [error, setError] = useState('');
  const current = useRef(conversationId); current.current = conversationId;
  const revision = useRef(0);
  useEffect(() => { revision.current += 1; setOpen(false); setResult(undefined); setError(''); setWorking(false); }, [conversationId]);
  async function generate() {
    if (!conversationId) return;
    const id = conversationId; const generation = revision.current;
    setWorking(true); setError('');
    try { const value = await generateOlderSummary(id); if (current.current === id && revision.current === generation) setResult(value); }
    catch { if (current.current === id && revision.current === generation) setError('当前没有可整理的较早对话，或今天的整理次数已用完。最近四条消息和工具记录会保留原文。'); }
    finally { if (current.current === id && revision.current === generation) setWorking(false); }
  }
  async function withdraw() {
    if (!conversationId) return;
    const id = conversationId; const generation = revision.current; setWorking(true); setError('');
    try { await withdrawOlderSummary(id); if (current.current === id && revision.current === generation) { setResult(undefined); setOpen(false); } }
    catch { if (current.current === id && revision.current === generation) setError('撤回未确认，请重试。'); }
    finally { if (current.current === id && revision.current === generation) setWorking(false); }
  }
  if (!conversationId) return null;
  return <>
    <Button size="small" disabled={busy || working} onClick={() => setOpen(true)}>整理较早对话</Button>
    <Modal open={open} title="较早对话摘要" onCancel={() => !working && setOpen(false)} footer={<Space wrap><Button disabled={working} onClick={() => setOpen(false)}>关闭</Button><Button disabled={working || busy} onClick={() => void withdraw()}>撤回摘要</Button><Button type="primary" loading={working} disabled={busy} onClick={() => void generate()}>确认整理</Button></Space>}>
      <p>从较早的普通消息中提取短摘录，保留原文，不调用模型。相同范围使用缓存，每天最多生成四次。</p>
      <p>摘要只有在设置中开启“较早对话的摘要”后才用于回复；来源变化时自动停用。</p>
      {error && <Alert type="error" message={error} />}
      {result && <div role="status"><p>{result.cached ? '已读取缓存' : '摘要已保存'} · 消息 {result.summary.from_message_id}–{result.summary.through_message_id}</p>
        {result.summary.items.map((item) => <p key={item.source_message_id}><Tag>{item.kind === 'user_statement' ? '用户陈述' : '模型推断'}</Tag>{item.excerpt}{item.truncated ? '…' : ''}</p>)}
        {result.summary.omitted_messages > 0 && <p>另有 {result.summary.omitted_messages} 条未纳入摘录，原文仍保留。</p>}
      </div>}
    </Modal>
  </>;
}

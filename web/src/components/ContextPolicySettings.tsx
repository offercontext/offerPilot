import { Alert, Switch, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { getContextPolicies, updateContextPolicies, type ContextSourceName } from '@/services/contextPolicies';

const sources: Array<[ContextSourceName, string, string]> = [
  ['confirmed_memory', '已确认的个人偏好', '使用你明确保存的偏好，当前请求始终优先。'],
  ['confirmed_readiness', '本次面试的准备重点', '仅在明确选择目标面试与确认版本后使用。'],
  ['knowledge_context', '知识笔记与来源证据', '按当前问题查找有效笔记与独立来源片段。'],
  ['older_conversation_summary', '较早对话的摘要', '使用你主动生成且来源仍有效的摘要。'],
];
export default function ContextPolicySettings() {
  const query = useQuery({ queryKey: ['context-policies'], queryFn: getContextPolicies });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function change(name: ContextSourceName, enabled: boolean) {
    if (!query.data) return;
    setBusy(true); setError('');
    try { await updateContextPolicies(query.data, name, enabled); await query.refetch(); }
    catch { setError('设置未确认，请刷新后重试。'); await query.refetch(); }
    finally { setBusy(false); }
  }
  return <section aria-labelledby="context-policy-title" style={{ background: 'var(--op-surface)', border: '1px solid var(--op-border)', borderRadius: 16, padding: 24, display: 'grid', gap: 16 }}>
    <Typography.Title id="context-policy-title" level={4} style={{ margin: 0 }}>回复使用的参考信息</Typography.Title>
    {(error || query.isError) && <Alert type="error" message={error || '参考信息设置读取失败'} />}
    {sources.map(([name, title, help]) => <div key={name} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, alignItems: 'center' }}>
      <div><div id={`${name}-label`}>{title}</div><Typography.Text type="secondary">{help}</Typography.Text></div>
      <Switch aria-labelledby={`${name}-label`} checked={query.data?.policies[name].enabled ?? false} disabled={!query.data || busy} onChange={(enabled) => void change(name, enabled)} />
    </div>)}
  </section>;
}

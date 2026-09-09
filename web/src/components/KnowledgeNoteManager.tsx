import { Alert, Button, Input, Modal, Space, Tag } from 'antd';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { listManagedKnowledgeNotes, mutateKnowledgeNote, type KnowledgeNoteMutation, type ManagedKnowledgeNote } from '@/services/knowledgeNoteLifecycle';

export default function KnowledgeNoteManager() {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ManagedKnowledgeNote>();
  const [title, setTitle] = useState('');
  const [blocks, setBlocks] = useState<Array<{ block_id: string; text: string }>>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['knowledge', 'note-management'], queryFn: listManagedKnowledgeNotes, enabled: open });
  const retry = useRef<{ key: string; command: KnowledgeNoteMutation }>();
  async function apply(note: ManagedKnowledgeNote, action: KnowledgeNoteMutation['action']) {
    const body = { action, expected_version_id: note.version_id, expected_archived: note.archived, confirmed: true as const,
      ...(action === 'revise' ? { title, blocks } : {}) };
    const key = JSON.stringify([note.id, body]);
    const command = retry.current?.key === key ? retry.current.command : { ...body, mutation_id: crypto.randomUUID() };
    retry.current = { key, command }; setBusy(true); setError('');
    try { await mutateKnowledgeNote(note.id, command); retry.current = undefined; setEditing(undefined); await cache.invalidateQueries({ queryKey: ['knowledge'] }); }
    catch { setError('操作未确认。可重试原操作；若版本已变化，请刷新后重新确认。'); throw new Error('knowledge_note_mutation_unconfirmed'); }
    finally { setBusy(false); }
  }
  return <>
    <Button onClick={() => setOpen(true)}>管理已确认的知识笔记</Button>
    <Modal open={open} title="已确认的知识笔记" width={720} onCancel={() => !busy && setOpen(false)} footer={<Button onClick={() => setOpen(false)} disabled={busy}>关闭</Button>}>
      {(error || query.isError) && <Alert type="error" message={error || '笔记读取失败'} action={<Button onClick={() => void query.refetch()}>刷新</Button>} />}
      {query.isLoading && <div role="status">正在读取笔记…</div>}
      {!query.isLoading && !query.data?.length && <p>还没有可管理的知识笔记。</p>}
      {query.data?.map((note) => <article key={note.id} style={{ borderBottom: '1px solid var(--op-border)', paddingBlock: 16 }}>
        <h3>{note.title} <Tag>{note.archived ? '已归档' : `版本 ${note.version_number}`}</Tag></h3>
        {note.content.blocks.map((block) => <p key={block.block_id} style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{block.text}</p>)}
        <Space wrap>
          <Button disabled={busy || note.archived} onClick={() => { setEditing(note); setTitle(note.title); setBlocks(note.content.blocks.map(({ block_id, text }) => ({ block_id, text }))); }}>编辑</Button>
          <Button disabled={busy} onClick={() => Modal.confirm({ title: note.archived ? '恢复这篇笔记？' : '归档这篇笔记？', content: '只有当前有效且未归档的版本会用于后续回复。', okText: '确认', cancelText: '取消', onOk: () => apply(note, note.archived ? 'unarchive' : 'archive') })}>{note.archived ? '恢复' : '归档'}</Button>
          <Button danger disabled={busy} onClick={() => Modal.confirm({ title: '删除这篇知识笔记？', content: '笔记及历史版本正文将删除，原始来源资料与 Evidence 保留。', okText: '确认删除', cancelText: '取消', onOk: () => apply(note, 'delete') })}>删除</Button>
        </Space>
      </article>)}
    </Modal>
    <Modal open={!!editing} title="编辑并确认新版本" okText="确认保存新版本" cancelText="取消" confirmLoading={busy} okButtonProps={{ disabled: !title.trim() || blocks.some((block) => !block.text.trim()) }} onCancel={() => !busy && setEditing(undefined)} onOk={() => editing && void apply(editing, 'revise').catch(() => undefined)}>
      <label htmlFor="knowledge-note-title">标题</label><Input id="knowledge-note-title" value={title} maxLength={120} onChange={(event) => setTitle(event.target.value)} disabled={busy} />
      {blocks.map((block, index) => <div key={block.block_id} style={{ marginTop: 16 }}><label htmlFor={`note-block-${index}`}>内容 {index + 1}</label><Input.TextArea id={`note-block-${index}`} value={block.text} maxLength={2000} autoSize={{ minRows: 3, maxRows: 8 }} disabled={busy} onChange={(event) => setBlocks((values) => values.map((value) => value.block_id === block.block_id ? { ...value, text: event.target.value } : value))} /></div>)}
      <p>来源引用保留。保存代表你确认本次修订。</p>
      {error && <Alert type="error" message={error} />}
    </Modal>
  </>;
}

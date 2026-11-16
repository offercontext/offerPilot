import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Input, Modal, Segmented, Select, Space, Tag } from 'antd';
import { ApartmentOutlined, UserOutlined, AimOutlined, BookOutlined, CodeOutlined, HeartOutlined, CalendarOutlined } from '@ant-design/icons';
import { t, useLocale, getLocale } from '@/i18n';
import { localizedModal } from '@/i18n/modal';
import { changeConfirmedMemory, getConfirmedMemory, listConfirmedMemory, type ConfirmedMemory, type MemoryMutation } from '@/services/confirmedMemory';
import styles from './MemoryProfile.module.css';

const groups = ['目标', '经历', '技能', '偏好', '近期准备'] as const;
const icons = [AimOutlined, BookOutlined, CodeOutlined, HeartOutlined, CalendarOutlined];
// A disposable browsing projection, never a stored assertion or a retrieval input.
export function memoryGroup(content: string): number {
  if (/准备|备考|复习|prepar|revision/i.test(content)) return 4;
  if (/目标|希望从事|求职|target|career|looking for/i.test(content)) return 0;
  if (/经历|项目|曾经|experience|project/i.test(content)) return 1;
  if (/技能|擅长|掌握|skill|proficien/i.test(content)) return 2;
  return 3;
}

export default function MemoryProfile({ embedded = false }: { embedded?: boolean }) {
  useLocale();
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ['confirmed-memory'], queryFn: listConfirmedMemory });
  const [mode, setMode] = useState('graph');
  const [search, setSearch] = useState('');
  const [state, setState] = useState('all');
  const [group, setGroup] = useState<number>();
  const [selectedId, setSelectedId] = useState<string>();
  const [editing, setEditing] = useState<ConfirmedMemory | 'new'>();
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const selected = query.data?.find(item => item.id === selectedId);
  const history = useQuery({ queryKey: ['confirmed-memory-history', selectedId, selected?.current_version], queryFn: () => getConfirmedMemory(selectedId!), enabled: historyOpen && !!selected });
  const items = (query.data ?? []).filter(item => (state === 'all' || item.state === state) && (group === undefined || memoryGroup(item.content) === group) && item.content.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const retry = useRef<{ key: string; command: MemoryMutation }>();
  function edit(item: ConfirmedMemory | 'new') {
    retry.current = undefined; setEditing(item); setContent(item === 'new' ? '' : item.content); setError('');
  }
  async function apply(item: ConfirmedMemory | undefined, action: MemoryMutation['action'], value = '') {
    if (inFlight.current) return;
    inFlight.current = true;
    const key = JSON.stringify([item?.id, item?.current_version ?? 0, action, value]);
    const command = retry.current?.key === key ? retry.current.command : { mutation_id: crypto.randomUUID(), action, expected_version: item?.current_version ?? 0, confirmed: true as const, content: value };
    retry.current = { key, command }; setBusy(true); setError('');
    try {
      const saved = await changeConfirmedMemory(item?.id, command);
      // Update the same authoritative query immediately; a failed refetch must not expose old text.
      cache.setQueryData<ConfirmedMemory[]>(['confirmed-memory'], current => saved.state === 'deleted' ? current?.filter(row => row.id !== saved.id) : [...(current ?? []).filter(row => row.id !== saved.id), saved]);
      cache.removeQueries({ queryKey: ['confirmed-memory-history'] });
      retry.current = undefined; setEditing(undefined); setHistoryOpen(false);
      setSelectedId(saved.state === 'deleted' ? undefined : saved.id);
      setNotice(action === 'delete' ? '记忆及历史正文已删除' : action === 'withdraw' ? '记忆已撤回，后续回复不再使用' : '记忆已确认保存');
      await cache.invalidateQueries({ queryKey: ['confirmed-memory'] });
    } catch {
      setError('操作未确认。可重试原操作；若内容已变化，请取消并刷新后重新确认。');
      throw new Error('memory_mutation_unconfirmed');
    } finally { inFlight.current = false; setBusy(false); }
  }
  function confirmAction(item: ConfirmedMemory, action: 'withdraw' | 'delete') {
    localizedModal('confirm', () => ({ title: action === 'delete' ? t('删除这条记忆？') : t('撤回这条记忆？'), content: action === 'delete' ? t('当前内容及所有历史版本正文都会删除，无法恢复。') : t('撤回后不再用于后续回复，历史版本保留。'), okText: action === 'delete' ? t('确认删除') : t('确认撤回'), cancelText: t('取消'), okButtonProps: { danger: action === 'delete' }, onOk: () => apply(item, action) }));
  }
  const renderItem = (item: ConfirmedMemory) => <button type="button" key={item.id} className={`${styles.node} ${selectedId === item.id ? styles.selected : ''}`} aria-pressed={selectedId === item.id} onClick={() => { setSelectedId(item.id); setHistoryOpen(false); }}><span>{item.content}</span><small>{item.state === 'active' ? t('已确认') : t('已撤回')}</small></button>;
  return <section className={styles.profile} aria-label={t('记忆画像')}>
    <header className={styles.header}><div><h1>{t('我的记忆画像')}</h1><p>{t('让 Pilot 更了解你，每一条记忆都由你掌控')}</p></div><Button type="primary" disabled={busy} onClick={() => edit('new')}>{t('添加记忆')}</Button></header>
    <div className={styles.toolbar}><Space wrap><Segmented aria-label={t('记忆视图')} value={mode} onChange={value => setMode(String(value))} options={[{ label: t('图谱'), value: 'graph' }, { label: t('列表'), value: 'list' }]} /><Button disabled={busy} loading={query.isFetching} onClick={() => { setHistoryOpen(false); void query.refetch(); }}>{t('刷新')}</Button></Space><p className={styles.explanation}>{t('仅展示用户明确确认的记忆，不自动导入 AI 推断或备考计划。分组按正文自动整理，仅用于浏览。')}</p></div>
    {notice && <div role="status">{t(notice)}</div>}
    {(query.isError || error) && <Alert type="error" showIcon message={error ? t(error) : t('记忆读取失败，请刷新重试')} />}
    <div className={`${styles.workspace} ${embedded ? styles.embedded : ''}`}>
      <div className={styles.canvas}>
        <div className={styles.filters}><Input allowClear aria-label={t('搜索记忆')} placeholder={t('搜索记忆')} value={search} onChange={event => setSearch(event.target.value)} /><Select aria-label={t('记忆状态')} value={state} onChange={setState} options={[{ value: 'all', label: t('全部状态') }, { value: 'active', label: t('已确认') }, { value: 'withdrawn', label: t('已撤回') }]} /><Select aria-label={t('记忆分组')} value={group ?? -1} onChange={value => setGroup(value < 0 ? undefined : value)} options={[{ value: -1, label: t('全部分组') }, ...groups.map((label, value) => ({ value, label: t(label) }))]} /></div>
        {query.isLoading ? <p role="status">{t('正在读取记忆…')}</p> : items.length === 0 ? <Empty description={t('没有匹配的记忆')} /> : mode === 'list' ? <div className={styles.list}>{items.map(renderItem)}</div> : <div className={styles.graph}>
          <svg className={styles.connections} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M50 51 L50 20 M50 51 L85 43 M50 51 L76 82 M50 51 L24 82 M50 51 L15 43" stroke="var(--op-primary)" strokeWidth="0.3" fill="none" /></svg><div className={styles.me}><UserOutlined /><strong>{t('我')}</strong></div>
          {groups.map((label, index) => { const rows = items.filter(item => memoryGroup(item.content) === index); const Icon = icons[index]; return <section key={label} className={`${styles.cluster} ${styles['cluster' + index]}`}><h2><Icon /> {t(label)} <small>{rows.length}</small></h2><div className={styles.leaves}>{rows.length ? rows.map(renderItem) : <span className={styles.noData}>{t('暂无记忆')}</span>}</div></section>; })}
        </div>}
      </div>
      <aside className={styles.detail} aria-label={t('记忆详情')}>
        {selected ? <><h2>{t('记忆详情')}</h2><Space wrap><Tag color="purple">{t(groups[memoryGroup(selected.content)])}</Tag><Tag color={selected.state === 'active' ? 'green' : undefined}>{selected.state === 'active' ? t('用户已确认') : t('已撤回')}</Tag></Space><h3>{t('记忆内容')}</h3><p className={styles.body}>{selected.content}</p><h3>{t('来源')}</h3><p>{t('用户明确确认')}</p><p className={styles.explanation}>{t('未记录原始对话链接；确认不代表外部事实已核验。')}</p><h3>{t('最近确认')}</h3><p>{selected.confirmed_at ? new Date(selected.confirmed_at).toLocaleString(getLocale()) : t('未记录')}</p><p>{t('版本 {version}', { version: selected.current_version })}</p><Space wrap className={styles.actions}><Button type="primary" disabled={busy} onClick={() => edit(selected)}>{selected.state === 'active' ? t('编辑') : t('重新确认')}</Button>{selected.state === 'active' && <Button disabled={busy} onClick={() => confirmAction(selected, 'withdraw')}>{t('撤回')}</Button>}<Button danger disabled={busy} onClick={() => confirmAction(selected, 'delete')}>{t('删除')}</Button><Button disabled={busy} onClick={() => setHistoryOpen(true)}>{t('查看版本')}</Button></Space></> : <div className={styles.placeholder}><ApartmentOutlined /><p>{t('选择一条记忆，查看内容与来源')}</p></div>}
      </aside>
    </div>
    <Modal open={editing !== undefined} title={t('编辑并确认记忆')} okText={t('确认保存')} cancelText={t('取消')} confirmLoading={busy} okButtonProps={{ disabled: !content.trim() || busy }} onCancel={() => { if (!busy) { retry.current = undefined; setEditing(undefined); setError(''); void query.refetch(); } }} onOk={() => void apply(editing === 'new' ? undefined : editing, 'confirm', content.trim()).catch(() => undefined)}>
      <label htmlFor="memory-content">{t('你希望 Pilot 记住的内容')}</label><Input.TextArea id="memory-content" value={content} onChange={event => setContent(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 4, maxRows: 10 }} disabled={busy} />
      {error && <Alert type="error" message={t(error)} />}
    </Modal>
    <Modal open={historyOpen && !!selected} title={t('已确认的版本记录')} footer={<Button onClick={() => setHistoryOpen(false)}>{t('关闭')}</Button>} onCancel={() => setHistoryOpen(false)}>
      {history.isLoading && <p role="status">{t('正在读取记忆…')}</p>}{history.isError && <Alert type="error" message={t('版本记录读取失败')} />}{!history.isError && history.data?.versions?.map(version => <div key={version.version}><h3>{t('版本 {version}', { version: version.version })}</h3><p>{new Date(version.confirmed_at).toLocaleString(getLocale())}</p><p className={styles.body}>{version.content}</p></div>)}
    </Modal>
  </section>;
}

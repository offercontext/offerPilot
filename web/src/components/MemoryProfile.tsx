import { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Alert, Button, Empty, Input, Modal, Segmented, Select, Space, Tag } from 'antd';
import { ApartmentOutlined, UserOutlined, AimOutlined, BookOutlined, CodeOutlined, HeartOutlined, CalendarOutlined } from '@ant-design/icons';
import { t, useLocale, getLocale } from '@/i18n';
import { localizedModal } from '@/i18n/modal';
import { changeConfirmedMemory, getConfirmedMemory, listConfirmedMemory, type ConfirmedMemory, type MemoryMutation, type MemoryCategory } from '@/services/confirmedMemory';
import styles from './MemoryProfile.module.css';

const groups = ['目标', '经历', '技能', '偏好', '近期准备', '未分类'] as const;
const categories: MemoryCategory[] = ['goal', 'experience', 'skill', 'preference', 'preparation', 'unclassified'];
const icons = [AimOutlined, BookOutlined, CodeOutlined, HeartOutlined, CalendarOutlined];
// Legacy records remain unclassified; browsing never invents persisted metadata.
export function memoryGroup(item: ConfirmedMemory): number {
  const index = categories.indexOf(item.metadata?.category ?? 'unclassified');
  return index < 0 ? 5 : index;
}
export function localExpiry(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}
function expiryLabel(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString(getLocale()) : t('未指定有效期');
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
  const [category, setCategory] = useState<MemoryCategory>('unclassified');
  const [validUntil, setValidUntil] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const selected = query.data?.find(item => item.id === selectedId);
  const history = useQuery({ queryKey: ['confirmed-memory-history', selectedId, selected?.current_version], queryFn: () => getConfirmedMemory(selectedId!), enabled: historyOpen && !!selected });
  const items = (query.data ?? []).filter(item => (state === 'all' || item.state === state) && (group === undefined || memoryGroup(item) === group) && item.content.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const retry = useRef<{ key: string; command: MemoryMutation }>();
  function edit(item: ConfirmedMemory | 'new') {
    retry.current = undefined; setEditing(item); setContent(item === 'new' ? '' : item.content); setCategory(item === 'new' ? 'unclassified' : item.metadata?.category ?? 'unclassified'); setValidUntil(item === 'new' ? '' : localExpiry(item.metadata?.valid_until)); setError('');
  }
  async function apply(item: ConfirmedMemory | undefined, action: MemoryMutation['action'], value = '') {
    if (inFlight.current) return;
    let metadata: MemoryMutation['metadata'];
    if (action === 'confirm') {
      if (validUntil && (!Number.isFinite(new Date(validUntil).getTime()) || localExpiry(new Date(validUntil).toISOString()) !== validUntil)) {
        setError('请输入有效的截止时间'); return;
      }
      const savedExpiry = item?.metadata?.valid_until;
      const expiry = savedExpiry && validUntil === localExpiry(savedExpiry) ? savedExpiry : validUntil ? new Date(validUntil).toISOString() : null;
      metadata = { category, valid_until: expiry };
    }
    inFlight.current = true;
    const key = JSON.stringify([item?.id, item?.current_version ?? 0, action, value, metadata]);
    const command = retry.current?.key === key ? retry.current.command : { mutation_id: crypto.randomUUID(), action, expected_version: item?.current_version ?? 0, confirmed: true as const, content: value, ...(metadata ? { metadata } : {}) };
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
  const renderItem = (item: ConfirmedMemory) => <button type="button" key={item.id} className={`${styles.node} ${selectedId === item.id ? styles.selected : ''}`} aria-pressed={selectedId === item.id} onClick={() => { setSelectedId(item.id); setHistoryOpen(false); }}><span>{item.content}</span><small>{item.state === 'active' ? (item.temporal_status === 'expired' ? t('已到期，不再召回') : t('已确认')) : t('已撤回')}</small></button>;
  return <section className={styles.profile} aria-label={t('记忆画像')}>
    <header className={styles.header}><div><h1>{t('我的记忆画像')}</h1><p>{t('让 Pilot 更了解你，每一条记忆都由你掌控')}</p></div><Button type="primary" disabled={busy} onClick={() => edit('new')}>{t('添加记忆')}</Button></header>
    <div className={styles.toolbar}><Space wrap><Segmented aria-label={t('记忆视图')} value={mode} onChange={value => setMode(String(value))} options={[{ label: t('图谱'), value: 'graph' }, { label: t('列表'), value: 'list' }]} /><Button disabled={busy} loading={query.isFetching} onClick={() => { setHistoryOpen(false); void query.refetch(); }}>{t('刷新')}</Button></Space><p className={styles.explanation}>{t('仅展示用户明确确认的记忆，不自动导入 AI 推断。分组由你确认，旧记录保持未分类。')}</p></div>
    {notice && <div role="status">{t(notice)}</div>}
    {(query.isError || error) && <Alert type="error" showIcon message={error ? t(error) : t('记忆读取失败，请刷新重试')} />}
    <div className={`${styles.workspace} ${embedded ? styles.embedded : ''}`}>
      <div className={styles.canvas}>
        <div className={styles.filters}><Input allowClear aria-label={t('搜索记忆')} placeholder={t('搜索记忆')} value={search} onChange={event => setSearch(event.target.value)} /><Select aria-label={t('记忆状态')} value={state} onChange={setState} options={[{ value: 'all', label: t('全部状态') }, { value: 'active', label: t('已确认') }, { value: 'withdrawn', label: t('已撤回') }]} /><Select aria-label={t('记忆分组')} value={group ?? -1} onChange={value => setGroup(value < 0 ? undefined : value)} options={[{ value: -1, label: t('全部分组') }, ...groups.map((label, value) => ({ value, label: t(label) }))]} /></div>
        {query.isLoading ? <p role="status">{t('正在读取记忆…')}</p> : items.length === 0 ? <Empty description={t('没有匹配的记忆')} /> : mode === 'list' ? <div className={styles.list}>{items.map(renderItem)}</div> : <div className={styles.graph}>
          <svg className={styles.connections} viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="M50 51 L50 20 M50 51 L85 43 M50 51 L76 82 M50 51 L24 82 M50 51 L15 43" stroke="var(--op-primary)" strokeWidth="0.3" fill="none" /></svg><div className={styles.me}><UserOutlined /><strong>{t('我')}</strong></div>
          {groups.slice(0, 5).map((label, index) => { const rows = items.filter(item => memoryGroup(item) === index); const Icon = icons[index]; return <section key={label} className={`${styles.cluster} ${styles['cluster' + index]}`}><h2><Icon /> {t(label)} <small>{rows.length}</small></h2><div className={styles.leaves}>{rows.length ? rows.map(renderItem) : <span className={styles.noData}>{t('暂无记忆')}</span>}</div></section>; })}
        </div>}
        {mode === 'graph' && items.some(item => memoryGroup(item) === 5) && <section className={`${styles.cluster} ${styles.unclassified}`}><h2><BookOutlined /> {t('未分类')}</h2><p className={styles.explanation}>{t('旧记录不会自动推断分类，可编辑后明确选择。')}</p><div className={styles.leaves}>{items.filter(item => memoryGroup(item) === 5).map(renderItem)}</div></section>}
      </div>
      <aside className={styles.detail} aria-label={t('记忆详情')}>
        {selected ? <><h2>{t('记忆详情')}</h2><Space wrap><Tag color="purple">{t(groups[memoryGroup(selected)])}</Tag><Tag color={selected.state === 'active' ? 'green' : undefined}>{selected.state === 'active' ? t('用户已确认') : t('已撤回')}</Tag></Space><h3>{t('记忆内容')}</h3><p className={styles.body}>{selected.content}</p><h3>{t('来源')}</h3><p>{t('用户明确确认')}</p><p className={styles.explanation}>{t('未记录原始对话链接；确认不代表外部事实已核验。')}</p><h3>{t('有效期')}</h3><p>{expiryLabel(selected.metadata?.valid_until)}</p>{selected.temporal_status === 'expired' && <Alert type="warning" message={t('已到期，不再召回')} description={t('记录和历史仍保留。编辑有效期并确认后可重新使用。')} />}<h3>{t('最近确认')}</h3><p>{selected.confirmed_at ? new Date(selected.confirmed_at).toLocaleString(getLocale()) : t('未记录')}</p><p>{t('版本 {version}', { version: selected.current_version })}</p><Space wrap className={styles.actions}><Button type="primary" disabled={busy} onClick={() => edit(selected)}>{selected.state === 'active' ? t('编辑') : t('重新确认')}</Button>{selected.state === 'active' && <Button disabled={busy} onClick={() => confirmAction(selected, 'withdraw')}>{t('撤回')}</Button>}<Button danger disabled={busy} onClick={() => confirmAction(selected, 'delete')}>{t('删除')}</Button><Button disabled={busy} onClick={() => setHistoryOpen(true)}>{t('查看版本')}</Button></Space></> : <div className={styles.placeholder}><ApartmentOutlined /><p>{t('选择一条记忆，查看内容与来源')}</p></div>}
      </aside>
    </div>
    <Modal open={editing !== undefined} title={t('编辑并确认记忆')} okText={t('确认保存')} cancelText={t('取消')} confirmLoading={busy} okButtonProps={{ disabled: !content.trim() || busy }} onCancel={() => { if (!busy) { retry.current = undefined; setEditing(undefined); setError(''); void query.refetch(); } }} onOk={() => void apply(editing === 'new' ? undefined : editing, 'confirm', content.trim()).catch(() => undefined)}>
      <label htmlFor="memory-content">{t('你希望 Pilot 记住的内容')}</label><Input.TextArea id="memory-content" value={content} onChange={event => setContent(event.target.value)} maxLength={2000} showCount autoSize={{ minRows: 4, maxRows: 10 }} disabled={busy} />
      <div className={styles.metadataFields}>
        <label htmlFor="memory-category">{t('记忆分类')}</label><select id="memory-category" value={category} disabled={busy} onChange={event => setCategory(event.target.value as MemoryCategory)}>{categories.map((value, index) => <option key={value} value={value}>{t(groups[index])}</option>)}</select>
        <label htmlFor="memory-valid-until">{t('有效期截止（本地时间，可选）')}</label><Input id="memory-valid-until" type="datetime-local" value={validUntil} onChange={event => setValidUntil(event.target.value)} disabled={busy} />
        <p className={styles.explanation}>{t('留空表示未指定有效期，不代表永久有效；到期只停止召回，不删除历史。')}</p>
      </div>
      {error && <Alert type="error" message={t(error)} />}
    </Modal>
    <Modal open={historyOpen && !!selected} title={t('已确认的版本记录')} footer={<Button onClick={() => setHistoryOpen(false)}>{t('关闭')}</Button>} onCancel={() => setHistoryOpen(false)}>
      <p className={styles.explanation}>{t('未记录原始对话链接；确认不代表外部事实已核验。')}</p>
      {history.isLoading && <p role="status">{t('正在读取记忆…')}</p>}{history.isError && <Alert type="error" message={t('版本记录读取失败')} />}{!history.isError && history.data?.versions?.map(version => <div key={version.version}><h3>{t('版本 {version}', { version: version.version })}</h3><p>{new Date(version.confirmed_at).toLocaleString(getLocale())}</p><p className={styles.body}>{version.content}</p><p>{t(groups[categories.indexOf(version.metadata?.category ?? 'unclassified')])} · {expiryLabel(version.metadata?.valid_until)}</p></div>)}
    </Modal>
  </section>;
}

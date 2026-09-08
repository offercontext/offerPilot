import { useMemo, useState } from 'react';
import { Button, Input, Popconfirm } from 'antd';
import {
  DeleteOutlined,
  EditOutlined,
  InboxOutlined,
  PlusOutlined,
  PushpinOutlined,
  SearchOutlined,
  UndoOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import type { Conversation } from '@/types/chat';
import type { UpdateConversationPayload } from '@/services/chat';
import {
  CONVERSATION_GROUP_KEYS,
  conversationModeLabel,
  filterConversationsByView,
  groupConversations,
  searchConversations,
  type ConversationGroupKey,
  type ConversationView,
} from './conversationList';
import styles from './ChatPanel.module.css';

interface Props {
  conversations: Conversation[];
  activeId?: number;
  showArchived: boolean;
  onViewChange: (showArchived: boolean) => void;
  onSelect: (id: number) => void;
  onNew: () => void;
  onDelete: (id: number) => void;
  onUpdate: (id: number, payload: UpdateConversationPayload) => void;
}

export const GROUP_LABELS: Record<ConversationGroupKey, string> = {
  pinned: '置顶',
  today: '今天',
  'previous-seven-days': '近 7 天',
  earlier: '更早',
};

export default function ThreadRail({
  conversations,
  activeId,
  showArchived,
  onViewChange,
  onSelect,
  onNew,
  onDelete,
  onUpdate,
}: Props) {
  const [query, setQuery] = useState('');
  const view: ConversationView = showArchived ? 'archived' : 'active';
  const conversationsInView = useMemo(
    () => filterConversationsByView(conversations, view),
    [conversations, view],
  );
  const visibleConversations = useMemo(
    () => searchConversations(conversationsInView, query),
    [conversationsInView, query],
  );
  const groups = useMemo(
    () => groupConversations(visibleConversations, new Date()),
    [visibleConversations],
  );

  function renameConversation(conversation: Conversation) {
    const title = window.prompt('重命名对话', conversation.title)?.trim();
    if (!title || title === conversation.title) return;
    onUpdate(conversation.id, { title });
  }

  function stopActionPropagation(event: React.SyntheticEvent) {
    event.stopPropagation();
  }

  function renderConversation(conversation: Conversation) {
    const isArchived = view === 'archived';
    const hasPendingAction = Boolean(conversation.pending_action);
    const archiveTitle = hasPendingAction
      ? '该对话有待确认操作，完成或取消后才能归档'
      : '归档对话';

    return (
      <div
        key={conversation.id}
        className={`${styles.thread} ${conversation.id === activeId ? styles.threadActive : ''}`}
      >
        <button
          type="button"
          className={styles.threadSelect}
          aria-label={`打开对话：${conversation.title}`}
          onClick={() => onSelect(conversation.id)}
        >
          <span className={styles.threadMain}>
            <span className={styles.threadTitle} title={conversation.title}>{conversation.title}</span>
            <span className={styles.threadMeta}>
              <span>{conversationModeLabel(conversation.mode)}</span>
              <span aria-hidden="true">·</span>
              <span>{dayjs(conversation.updated_at).format('M月D日')}</span>
              {hasPendingAction ? <span className={styles.pendingBadge}>待确认</span> : null}
            </span>
            {conversation.context_label ? (
              <span className={styles.threadContext} title={conversation.context_label}>
                {conversation.context_label}
              </span>
            ) : null}
          </span>
        </button>
        <div className={styles.threadActions} onClick={stopActionPropagation}>
          <Button
            type="text"
            className={styles.threadAction}
            aria-label={conversation.pinned_at ? '取消置顶对话' : '置顶对话'}
            title={conversation.pinned_at ? '取消置顶' : '置顶'}
            icon={<PushpinOutlined />}
            onKeyDown={stopActionPropagation}
            onClick={(event) => {
              event.stopPropagation();
              onUpdate(conversation.id, { pinned: !conversation.pinned_at });
            }}
          />
          <Button
            type="text"
            className={styles.threadAction}
            aria-label="重命名对话"
            title="重命名"
            icon={<EditOutlined />}
            onKeyDown={stopActionPropagation}
            onClick={(event) => {
              event.stopPropagation();
              renameConversation(conversation);
            }}
          />
          {isArchived ? (
            <Button
              type="text"
              className={styles.threadAction}
              aria-label="恢复对话"
              title="恢复对话"
              icon={<UndoOutlined />}
              onKeyDown={stopActionPropagation}
              onClick={(event) => {
                event.stopPropagation();
                onUpdate(conversation.id, { archived: false });
              }}
            />
          ) : (
            <Button
              type="text"
              className={styles.threadAction}
              aria-label={archiveTitle}
              title={archiveTitle}
              disabled={hasPendingAction}
              icon={<InboxOutlined />}
              onKeyDown={stopActionPropagation}
              onClick={(event) => {
                event.stopPropagation();
                onUpdate(conversation.id, { archived: true });
              }}
            />
          )}
          <Popconfirm
            title="删除该对话？"
            okText="删除"
            cancelText="取消"
            onConfirm={(event) => {
              event?.stopPropagation();
              onDelete(conversation.id);
            }}
            onCancel={(event) => event?.stopPropagation()}
          >
            <Button
              type="text"
              danger
              className={styles.threadAction}
              aria-label="删除对话"
              title="删除对话"
              icon={<DeleteOutlined />}
              onKeyDown={stopActionPropagation}
              onClick={stopActionPropagation}
            />
          </Popconfirm>
        </div>
      </div>
    );
  }

  const emptyLabel = query.trim()
    ? '没有匹配的对话'
    : view === 'archived'
      ? '暂无已归档对话'
      : '暂无对话';

  return (
    <aside className={styles.rail}>
      <Button className={styles.railNew} block size="large" icon={<PlusOutlined />} onClick={onNew}>
        新建对话
      </Button>
      <Input
        className={styles.railSearch}
        size="large"
        allowClear
        prefix={<SearchOutlined aria-hidden="true" />}
        aria-label="搜索对话"
        placeholder="搜索对话"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <div className={styles.railViewToggle} role="group" aria-label="对话视图">
        <Button
          type={view === 'active' ? 'primary' : 'text'}
          aria-pressed={view === 'active'}
          onClick={() => onViewChange(false)}
        >
          进行中
        </Button>
        <Button
          type={view === 'archived' ? 'primary' : 'text'}
          aria-pressed={view === 'archived'}
          onClick={() => onViewChange(true)}
        >
          已归档
        </Button>
      </div>
      <div className={styles.railThreads}>
        {visibleConversations.length === 0 ? (
          <div className={styles.railEmpty}>{emptyLabel}</div>
        ) : (
          CONVERSATION_GROUP_KEYS.map((key) => {
            const conversationsInGroup = groups[key];
            if (conversationsInGroup.length === 0) return null;
            return (
              <section key={key} className={styles.threadGroup} aria-labelledby={`thread-group-${key}`}>
                <div className={styles.railLabel} id={`thread-group-${key}`}>
                  <span>{GROUP_LABELS[key]}</span>
                  <span className={styles.groupCount}>{conversationsInGroup.length}</span>
                </div>
                {conversationsInGroup.map(renderConversation)}
              </section>
            );
          })
        )}
      </div>
    </aside>
  );
}

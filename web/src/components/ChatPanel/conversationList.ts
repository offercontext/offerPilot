import type { Conversation } from '@/types/chat';

export type ConversationView = 'active' | 'archived';
export type ConversationGroupKey = 'pinned' | 'today' | 'previous-seven-days' | 'earlier';
export type ConversationGroups = Record<ConversationGroupKey, Conversation[]>;

export const CONVERSATION_GROUP_KEYS: ConversationGroupKey[] = [
  'pinned',
  'today',
  'previous-seven-days',
  'earlier',
];

export function conversationModeLabel(mode?: string): string {
  return mode === 'nego_coach' ? '谈薪教练' : '通用';
}

function conversationSearchText(conversation: Conversation): string {
  const context = conversation.context_label?.trim()
    || [conversation.context_type, conversation.context_ref].filter(Boolean).join(' ');
  return [
    conversation.title,
    conversation.mode ?? '',
    conversationModeLabel(conversation.mode),
    context,
    conversation.context_type,
    conversation.context_ref,
    conversation.pending_action ? '待确认 pending' : '',
  ]
    .join(' ')
    .toLocaleLowerCase();
}

export function searchConversations(
  conversations: Conversation[],
  query: string,
): Conversation[] {
  const normalized = query.trim().toLocaleLowerCase();
  if (!normalized) return conversations;
  return conversations.filter((conversation) => conversationSearchText(conversation).includes(normalized));
}

export function filterConversationsByView(
  conversations: Conversation[],
  view: ConversationView,
): Conversation[] {
  return conversations.filter((conversation) =>
    view === 'archived' ? Boolean(conversation.archived_at) : !conversation.archived_at,
  );
}

function compareConversationRecency(left: Conversation, right: Conversation): number {
  const timeDifference = Date.parse(right.updated_at) - Date.parse(left.updated_at);
  return timeDifference || right.id - left.id;
}

export function groupConversations(
  conversations: Conversation[],
  now: Date,
): ConversationGroups {
  const groups: ConversationGroups = {
    pinned: [],
    today: [],
    'previous-seven-days': [],
    earlier: [],
  };
  const todayStart = new Date(now);
  todayStart.setHours(0, 0, 0, 0);
  const previousSevenDaysStart = new Date(todayStart);
  previousSevenDaysStart.setDate(previousSevenDaysStart.getDate() - 7);

  for (const conversation of conversations) {
    if (conversation.pinned_at) {
      groups.pinned.push(conversation);
      continue;
    }
    const updatedAt = new Date(conversation.updated_at);
    if (updatedAt >= todayStart) {
      groups.today.push(conversation);
    } else if (updatedAt >= previousSevenDaysStart) {
      groups['previous-seven-days'].push(conversation);
    } else {
      groups.earlier.push(conversation);
    }
  }

  for (const key of CONVERSATION_GROUP_KEYS) groups[key].sort(compareConversationRecency);
  return groups;
}

export function firstPendingConversationId(
  conversations: Conversation[],
): number | undefined {
  return conversations
    .filter((conversation) => conversation.pending_action)
    .sort(compareConversationRecency)[0]?.id;
}

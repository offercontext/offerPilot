import { describe, expect, it } from 'vitest';
import type { Conversation } from '@/types/chat';
import {
  filterConversationsByView,
  firstPendingConversationId,
  groupConversations,
  searchConversations,
} from './conversationList';

function conversation(id: number, overrides: Partial<Conversation> = {}): Conversation {
  return {
    id,
    title: `对话 ${id}`,
    mode: 'general',
    context_type: 'workspace',
    context_ref: '',
    created_at: '2026-07-01T00:00:00.000Z',
    updated_at: '2026-07-01T00:00:00.000Z',
    ...overrides,
  };
}

describe('conversation search and views', () => {
  const items = [
    conversation(1, { title: '字节面试复盘', context_label: '字节跳动 · 后端工程师' }),
    conversation(2, { mode: 'nego_coach', context_type: 'application', context_ref: '42' }),
    conversation(3, {
      title: '等待写入',
      pending_action: {
        tool_name: 'update_application_status',
        human: '更新状态',
        confirmation_token: 'pending-token',
      },
    }),
    conversation(4, { archived_at: '2026-07-09T00:00:00.000Z' }),
  ];

  it('searches case-insensitively across title, localized mode, context label, fallback context, and pending terms', () => {
    expect(searchConversations(items, '字节').map((item) => item.id)).toEqual([1]);
    expect(searchConversations(items, '谈薪教练').map((item) => item.id)).toEqual([2]);
    expect(searchConversations(items, 'NEGO_COACH').map((item) => item.id)).toEqual([2]);
    expect(searchConversations(items, 'APPLICATION').map((item) => item.id)).toEqual([2]);
    expect(searchConversations(items, '42').map((item) => item.id)).toEqual([2]);
    expect(searchConversations(items, 'PENDING').map((item) => item.id)).toEqual([3]);
    expect(searchConversations(items, '待确认').map((item) => item.id)).toEqual([3]);
  });

  it('separates active and archived conversations without leaking either view', () => {
    expect(filterConversationsByView(items, 'active').map((item) => item.id)).toEqual([1, 2, 3]);
    expect(filterConversationsByView(items, 'archived').map((item) => item.id)).toEqual([4]);
  });
});

describe('conversation grouping', () => {
  it('groups each conversation exactly once using injected local calendar boundaries', () => {
    const now = new Date('2026-07-10T12:00:00');
    const groups = groupConversations(
      [
        conversation(1, { pinned_at: '2026-06-01T00:00:00Z', updated_at: '2026-06-01T00:00:00Z' }),
        conversation(2, { updated_at: '2026-07-10T00:01:00' }),
        conversation(3, { updated_at: '2026-07-09T23:59:00' }),
        conversation(4, { updated_at: '2026-07-03T00:00:00' }),
        conversation(5, { updated_at: '2026-07-02T23:59:59' }),
      ],
      now,
    );

    expect(Object.keys(groups)).toEqual(['pinned', 'today', 'previous-seven-days', 'earlier']);
    expect(groups.pinned.map((item) => item.id)).toEqual([1]);
    expect(groups.today.map((item) => item.id)).toEqual([2]);
    expect(groups['previous-seven-days'].map((item) => item.id)).toEqual([3, 4]);
    expect(groups.earlier.map((item) => item.id)).toEqual([5]);
    expect(Object.values(groups).flat().map((item) => item.id).sort()).toEqual([1, 2, 3, 4, 5]);
  });

  it('sorts every group by updated time descending with an id tiebreaker', () => {
    const groups = groupConversations(
      [
        conversation(7, { updated_at: '2026-07-10T09:00:00' }),
        conversation(9, { updated_at: '2026-07-10T09:00:00' }),
        conversation(8, { updated_at: '2026-07-10T10:00:00' }),
      ],
      new Date('2026-07-10T12:00:00'),
    );

    expect(groups.today.map((item) => item.id)).toEqual([8, 9, 7]);
  });
});

describe('pending conversation recovery', () => {
  it('selects the most recently updated pending conversation independent of input order', () => {
    const pending = {
      tool_name: 'create_application',
      human: '创建投递',
      confirmation_token: 'pending-token',
    };
    const older = conversation(1, { updated_at: '2026-07-09T10:00:00Z', pending_action: pending });
    const newer = conversation(2, { updated_at: '2026-07-09T11:00:00Z', pending_action: pending });

    expect(firstPendingConversationId([older, newer])).toBe(2);
    expect(firstPendingConversationId([newer, older])).toBe(2);
  });
});

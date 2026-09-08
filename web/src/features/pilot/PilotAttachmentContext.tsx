import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useReducer,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  addPilotAttachment,
  emptyPilotAttachmentDraft,
  removePilotAttachment,
  type PilotAttachmentDraft,
} from '@/lib/pilotAttachments';
import type { PilotContextAttachment } from '@/types/chat';

export type PilotAttachmentConversationKey = `conversation:${number}` | `new:${string}`;

export interface PilotAttachmentState {
  drafts: Partial<Record<PilotAttachmentConversationKey, PilotAttachmentDraft>>;
}

export type PilotAttachmentAction =
  | { type: 'add'; key: PilotAttachmentConversationKey; attachment: PilotContextAttachment }
  | { type: 'remove'; key: PilotAttachmentConversationKey; attachmentOrKey: PilotContextAttachment | string }
  | { type: 'clear'; key: PilotAttachmentConversationKey };

export interface PilotAttachmentStore {
  drafts: PilotAttachmentState['drafts'];
  addAttachment: (key: PilotAttachmentConversationKey, attachment: PilotContextAttachment) => void;
  removeAttachment: (
    key: PilotAttachmentConversationKey,
    attachmentOrKey: PilotContextAttachment | string,
  ) => void;
  clearAttachmentsByKey: (key: PilotAttachmentConversationKey) => void;
  createNewDraftKey: () => PilotAttachmentConversationKey;
  createNewDraftWithAttachment: (attachment: PilotContextAttachment) => PilotAttachmentConversationKey;
}

export interface PilotAttachmentContextValue {
  activeKey?: PilotAttachmentConversationKey;
  attachments: PilotContextAttachment[];
  notice?: string;
  setActiveConversationKey: (key?: PilotAttachmentConversationKey) => void;
  addAttachment: (attachment: PilotContextAttachment) => void;
  removeAttachment: (attachmentOrKey: PilotContextAttachment | string) => void;
  clearAttachments: () => void;
  clearAttachmentsByKey: (key: PilotAttachmentConversationKey) => void;
  beginNewAttachmentDraft: () => PilotAttachmentConversationKey;
  ensureNewAttachmentDraft: () => PilotAttachmentConversationKey;
}

const PilotAttachmentContext = createContext<PilotAttachmentStore | null>(null);

export function emptyPilotAttachmentState(): PilotAttachmentState {
  return { drafts: {} };
}

function withDraft(
  state: PilotAttachmentState,
  key: PilotAttachmentConversationKey,
  draft: PilotAttachmentDraft,
): PilotAttachmentState {
  return { ...state, drafts: { ...state.drafts, [key]: draft } };
}

export function pilotAttachmentStateReducer(
  state: PilotAttachmentState,
  action: PilotAttachmentAction,
): PilotAttachmentState {
  const currentDraft = state.drafts[action.key] ?? emptyPilotAttachmentDraft();

  switch (action.type) {
    case 'add':
      return withDraft(state, action.key, addPilotAttachment(currentDraft, action.attachment));
    case 'remove':
      return withDraft(
        state,
        action.key,
        removePilotAttachment(currentDraft, action.attachmentOrKey),
      );
    case 'clear':
      return withDraft(state, action.key, emptyPilotAttachmentDraft());
  }
}

export function PilotAttachmentProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(pilotAttachmentStateReducer, undefined, emptyPilotAttachmentState);
  const nextNewDraftKeyRef = useRef(0);
  const addAttachment = useCallback(
    (key: PilotAttachmentConversationKey, attachment: PilotContextAttachment) =>
      dispatch({ type: 'add', key, attachment }),
    [],
  );
  const removeAttachment = useCallback(
    (key: PilotAttachmentConversationKey, attachmentOrKey: PilotContextAttachment | string) =>
      dispatch({ type: 'remove', key, attachmentOrKey }),
    [],
  );
  const clearAttachmentsByKey = useCallback(
    (key: PilotAttachmentConversationKey) => dispatch({ type: 'clear', key }),
    [],
  );
  const createNewDraftKey = useCallback(() => {
    nextNewDraftKeyRef.current += 1;
    return `new:draft-${nextNewDraftKeyRef.current}` as PilotAttachmentConversationKey;
  }, []);
  const createNewDraftWithAttachment = useCallback((attachment: PilotContextAttachment) => {
    nextNewDraftKeyRef.current += 1;
    const key = `new:draft-${nextNewDraftKeyRef.current}` as PilotAttachmentConversationKey;
    dispatch({ type: 'add', key, attachment });
    return key;
  }, []);
  const store = useMemo<PilotAttachmentStore>(
    () => ({
      drafts: state.drafts,
      addAttachment,
      removeAttachment,
      clearAttachmentsByKey,
      createNewDraftKey,
      createNewDraftWithAttachment,
    }),
    [
      state.drafts,
      addAttachment,
      removeAttachment,
      clearAttachmentsByKey,
      createNewDraftKey,
      createNewDraftWithAttachment,
    ],
  );

  return <PilotAttachmentContext.Provider value={store}>{children}</PilotAttachmentContext.Provider>;
}

export function usePilotAttachmentStore(): PilotAttachmentStore {
  const store = useContext(PilotAttachmentContext);
  if (!store) throw new Error('usePilotAttachmentStore must be used within PilotAttachmentProvider');
  return store;
}

export function usePilotAttachments(
  initialKey?: PilotAttachmentConversationKey,
): PilotAttachmentContextValue {
  const store = usePilotAttachmentStore();

  const [activeKey, setActiveKey] = useState<PilotAttachmentConversationKey | undefined>(initialKey);
  const activeDraft = activeKey ? store.drafts[activeKey] ?? emptyPilotAttachmentDraft() : undefined;
  const setActiveConversationKey = useCallback((key?: PilotAttachmentConversationKey) => {
    setActiveKey(key);
  }, []);
  const beginNewAttachmentDraft = useCallback(() => {
    const key = store.createNewDraftKey();
    setActiveKey(key);
    return key;
  }, [store.createNewDraftKey]);
  const ensureNewAttachmentDraft = useCallback(() => {
    if (activeKey?.startsWith('new:')) return activeKey;
    const key = store.createNewDraftKey();
    setActiveKey(key);
    return key;
  }, [activeKey, store.createNewDraftKey]);
  const addAttachment = useCallback(
    (attachment: PilotContextAttachment) => {
      if (activeKey) store.addAttachment(activeKey, attachment);
    },
    [activeKey, store.addAttachment],
  );
  const removeAttachment = useCallback(
    (attachmentOrKey: PilotContextAttachment | string) => {
      if (activeKey) store.removeAttachment(activeKey, attachmentOrKey);
    },
    [activeKey, store.removeAttachment],
  );
  const clearAttachments = useCallback(() => {
    if (activeKey) store.clearAttachmentsByKey(activeKey);
  }, [activeKey, store.clearAttachmentsByKey]);

  return useMemo(
    () => ({
      activeKey,
      attachments: activeDraft?.attachments ?? [],
      notice: activeDraft?.message,
      setActiveConversationKey,
      addAttachment,
      removeAttachment,
      clearAttachments,
      clearAttachmentsByKey: store.clearAttachmentsByKey,
      beginNewAttachmentDraft,
      ensureNewAttachmentDraft,
    }),
    [
      activeKey,
      activeDraft?.attachments,
      activeDraft?.message,
      setActiveConversationKey,
      addAttachment,
      removeAttachment,
      clearAttachments,
      store.clearAttachmentsByKey,
      beginNewAttachmentDraft,
      ensureNewAttachmentDraft,
    ],
  );
}

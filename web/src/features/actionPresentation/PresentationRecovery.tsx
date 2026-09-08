interface Props {
  failed: boolean;
  busy: boolean;
  onRefresh: () => void;
}

/** Recovery belongs to the conversation owner, independently of action cards. */
export function PresentationRecovery({ failed, busy, onRefresh }: Props) {
  if (!failed) return null;
  return (
    <div role="status">
      <span>操作状态暂时无法加载，已显示最新消息。</span>{' '}
      <button type="button" disabled={busy} onClick={onRefresh}>重新加载操作状态</button>
    </div>
  );
}

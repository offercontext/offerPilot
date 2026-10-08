import { useEffect, useRef } from 'react';
import type { UpdateSafetySnapshot } from './types';

/** Owner-only, request-time read: no stale snapshot is stored in the main process. */
export function useDesktopUpdateSafety(readSnapshot: () => UpdateSafetySnapshot | Promise<UpdateSafetySnapshot>) {
  const bridge = window.offerpilotUpdates;
  const current = useRef(readSnapshot);
  current.current = readSnapshot;

  useEffect(() => {
    if (!bridge) return;
    let active = true;
    const off = bridge.onPrepareInstall((request) => {
      if (typeof request?.id !== 'string' || !request.id || request.id.length > 128) return;
      void (async () => {
        let snapshot: UpdateSafetySnapshot;
        try {
          snapshot = await current.current();
        } catch {
          snapshot = { ready: false, hasDraft: true, activeRun: true, pendingApproval: true, reason: '无法读取安装安全状态。' };
        }
        if (active) bridge.replyPrepareInstall(request.id, snapshot);
      })();
    });
    return () => { active = false; off(); };
  }, [bridge]);
}

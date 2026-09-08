import { useEffect, useState } from 'react';
import type { ActionPresentationV1 } from './contracts';
import { getProductPresentation } from './service';

export function useProductPresentation(operationId: string, revision: string, busy: boolean) {
  const [loaded, setLoaded] = useState<{ operationId: string; revision: string; action: ActionPresentationV1 } | null>(null);
  useEffect(() => {
    let current = true;
    setLoaded(null);
    if (!busy) void getProductPresentation(operationId).then((action) => {
      if (current) setLoaded({ operationId, revision, action });
    }).catch(() => { /* The existing owner still supports older servers. */ });
    return () => { current = false; };
  }, [operationId, revision, busy]);
  return !busy && loaded?.operationId === operationId && loaded.revision === revision ? loaded.action : null;
}

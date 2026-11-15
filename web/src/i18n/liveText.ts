import { createElement } from 'react';
import { useLocale } from './index';

function LiveText({ render }: { render: () => string }) {
  useLocale();
  return render();
}
/** Keep authored transient copy live without restarting a toast or its timer. */
export function liveText(render: () => string) {
  return createElement(LiveText, { render });
}

// Read-only, bounded observations for the intermittent installed Drawer close
// interception. No HTML, labels, class strings, URLs, or values leave the page.
export function installDrawerCloseObservation(node, { key }) {
  if (window[key]) throw new Error('drawer close observer already installed');
  const doc = node.ownerDocument;
  const number = value => Number.isFinite(value) ? Math.max(-100000, Math.min(100000, Math.round(value * 100) / 100)) : null;
  const box = element => {
    const rect = element.getBoundingClientRect();
    return { x: number(rect.left), y: number(rect.top), width: number(rect.width), height: number(rect.height) };
  };
  const kind = element => {
    if (!element) return 'none';
    if (element === node) return 'target';
    if (typeof element.closest !== 'function') return 'other-element';
    if (node.contains(element)) return 'target';
    if (element === doc.documentElement) return 'document-root';
    if (element === doc.body) return 'document-body';
    for (const [selector, name] of [['.ant-drawer-mask', 'drawer-mask'], ['.ant-modal-mask', 'modal-mask'],
      ['.ant-drawer-header', 'drawer-header'], ['.ant-drawer-body', 'drawer-body'],
      ['.ant-drawer-content', 'drawer-content'], ['.ant-drawer-section', 'drawer-content'],
      ['.ant-drawer-content-wrapper', 'drawer-wrapper'], ['.ant-drawer', 'drawer-root'],
      ['.op-topbar', 'app-topbar'], ['[role="tooltip"]', 'tooltip'], ['.ant-popover', 'popover'],
      ['.ant-select-dropdown', 'select-popup'], ['[role="dialog"]', 'other-dialog'],
      ['button, [role="button"]', 'other-button']]) {
      if (element.closest(selector)) return name;
    }
    return 'other-element';
  };
  const describe = element => {
    const css = getComputedStyle(element);
    const animations = element.getAnimations();
    return { kind: kind(element), ...box(element),
      pointerEvents: css.pointerEvents === 'none' ? 'none' : css.pointerEvents === 'auto' ? 'auto' : 'other',
      visible: Boolean(element.getClientRects().length) && css.visibility === 'visible', opacity: number(Number(css.opacity)),
      position: ['static', 'relative', 'absolute', 'fixed', 'sticky'].includes(css.position) ? css.position : 'other',
      zIndex: css.zIndex === 'auto' ? 'auto' : number(Number(css.zIndex)), transformed: css.transform !== 'none',
      runningFiniteAnimations: Math.min(32, animations.filter(animation => animation.playState === 'running'
        && animation.effect?.getTiming().iterations !== Infinity).length),
    };
  };
  const state = { events: [], pointerMoves: [], droppedEvents: 0, droppedPointerMoves: 0, sequence: 0 };
  const observe = event => {
    state.sequence = Math.min(10000, state.sequence + 1);
    const moving = event.type === 'pointermove';
    const events = moving ? state.pointerMoves : state.events;
    if (events.length >= (moving ? 4 : 12)) {
      const dropped = moving ? 'droppedPointerMoves' : 'droppedEvents';
      state[dropped] = Math.min(10000, state[dropped] + 1); return;
    }
    events.push({ sequence: state.sequence, type: event.type, trusted: event.isTrusted === true, target: kind(event.target),
      x: number(event.clientX), y: number(event.clientY), button: number(event.button) });
  };
  const eventTypes = ['pointermove', 'pointerdown', 'pointerup', 'click'];
  state.read = () => {
    const rect = node.getBoundingClientRect();
    const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
    const top = doc.elementFromPoint(x, y);
    const ancestors = [];
    for (let parent = node.parentElement; parent && ancestors.length < 12; parent = parent.parentElement) {
      ancestors.push(describe(parent));
    }
    const drawer = node.closest('.ant-drawer');
    return { connected: node.isConnected, control: describe(node),
      centerWithinViewport: x >= 0 && y >= 0 && x < innerWidth && y < innerHeight,
      centerReceiver: kind(top), hitStack: doc.elementsFromPoint(x, y).slice(0, 8).map(describe), ancestors,
      drawerOpenClass: Boolean(drawer?.classList.contains('ant-drawer-open')),
      drawerVisible: Boolean(drawer?.getClientRects().length),
      documentFocused: doc.hasFocus(), visibilityState: ['visible', 'hidden'].includes(doc.visibilityState) ? doc.visibilityState : 'other',
      viewport: { width: number(innerWidth), height: number(innerHeight) },
      events: state.events.map(event => ({ ...event })), droppedEvents: state.droppedEvents,
      pointerMoves: state.pointerMoves.map(event => ({ ...event })), droppedPointerMoves: state.droppedPointerMoves };
  };
  state.dispose = () => { for (const type of eventTypes) doc.removeEventListener(type, observe, true); };
  window[key] = state;
  try {
    for (const type of eventTypes) doc.addEventListener(type, observe, true);
    return state.read();
  } catch (error) { state.dispose(); delete window[key]; throw error; }
}

export function readDrawerCloseObservation({ key, dispose = false }) {
  const state = window[key];
  if (!state) throw new Error('drawer close observer missing');
  try { return state.read(); }
  finally { if (dispose) { state.dispose(); delete window[key]; } }
}

// Classify the action log in memory. Never publish the raw Playwright error,
// which may contain snippets of unrelated DOM or synthetic/private text.
export function drawerCloseFailure(error) {
  const message = String(error?.message || '');
  return { timeout: error?.name === 'TimeoutError',
    intercepted: /intercepts pointer events/i.test(message),
    notVisible: /not visible/i.test(message), notStable: /not stable/i.test(message),
    outsideViewport: /outside of the viewport/i.test(message), detached: /not attached|detached/i.test(message),
    knownInterceptor: /op-topbar/.test(message) ? 'app-topbar'
      : /ant-drawer-mask/.test(message) ? 'drawer-mask'
        : /ant-drawer-content-wrapper/.test(message) ? 'drawer-wrapper'
          : /ant-modal-mask/.test(message) ? 'modal-mask' : 'unclassified' };
}

// Serialized DOM observation. Never retain text, HTML, class strings, URLs or values.
export function measureControlHit(node) {
  const rect = node.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const css = getComputedStyle(node);
  const top = document.elementFromPoint(x, y);
  const same = top === node || Boolean(top && node.contains(top));
  const receiver = same ? 'target' : !top ? 'none' : top.closest('.ant-drawer-mask') ? 'drawer-mask'
    : top.closest('[role="dialog"]') ? 'other-dialog-element' : top.closest('button') ? 'other-button' : 'other-element';
  let ancestorHidden = false;
  let ancestorPointerDisabled = false;
  for (let ancestor = node.parentElement; ancestor; ancestor = ancestor.parentElement) {
    const style = getComputedStyle(ancestor);
    ancestorHidden ||= style.visibility !== 'visible' || Number(style.opacity) === 0;
    ancestorPointerDisabled ||= style.pointerEvents === 'none';
  }
  return { width: rect.width, height: rect.height, x: rect.left, y: rect.top,
    inViewport: x >= 0 && y >= 0 && x < innerWidth && y < innerHeight,
    visible: Boolean(node.getClientRects().length) && css.visibility === 'visible' && Number(css.opacity) > 0,
    enabled: !node.disabled, pointerEventsEnabled: css.pointerEvents !== 'none',
    ancestorHidden, ancestorPointerDisabled, receiver };
}

// Fixed numeric observations only. A local horizontal scroll must never be
// mistaken for page-wide overflow or a control that remains beyond the viewport.
export function measureScrollableAncestors(node) {
  const scrollers = [];
  let depth = 0;
  let controlFullyWithinScrollableBounds = true;
  const box = node.getBoundingClientRect();
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    depth++;
    const css = getComputedStyle(parent);
    const bounds = parent.getBoundingClientRect();
    const left = bounds.left + parent.clientLeft;
    const top = bounds.top + parent.clientTop;
    if (/(auto|scroll|hidden|clip)/.test(css.overflowX) && (box.left < left - 1 || box.right > left + parent.clientWidth + 1)) controlFullyWithinScrollableBounds = false;
    if (/(auto|scroll|hidden|clip)/.test(css.overflowY) && (box.top < top - 1 || box.bottom > top + parent.clientHeight + 1)) controlFullyWithinScrollableBounds = false;
    if (parent.scrollWidth > parent.clientWidth + 1 && /(auto|scroll)/.test(css.overflowX)) {
      scrollers.push({ depth, scrollLeft: parent.scrollLeft, clientWidth: parent.clientWidth,
        scrollWidth: parent.scrollWidth, documentScroller: parent === document.scrollingElement });
    }
  }
  return { width: innerWidth, documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    controlFullyWithinViewport: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
    controlFullyWithinScrollableBounds, scrollers };
}

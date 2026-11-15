// Serialized read-only observation. Let the application receive its own motion
// lifecycle events: finishing a paused rc-motion animation before its active
// phase can consume animationend before the component is ready to remove it.
export function naturalScreenshotPaintReady() {
  const unsettled = document.getAnimations().some(animation => {
    const end = animation.effect?.getComputedTiming().endTime;
    return Number.isFinite(end) && (animation.pending || animation.playState === 'running' || animation.playState === 'paused');
  });
  const reset = () => { delete window.__offerpilotScreenshotPaint; return false; };
  if (unsettled) return reset();
  const surfaces = [...document.querySelectorAll('[role="dialog"], [role="menu"], .ant-drawer-content-wrapper')]
    .filter(node => node.getClientRects().length && getComputedStyle(node).visibility === 'visible');
  const boxes = [];
  for (const node of surfaces) {
    for (let ancestor = node; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      if (Number(style.opacity) < 0.999 || style.visibility !== 'visible') return reset();
    }
    const rect = node.getBoundingClientRect();
    boxes.push([rect.x, rect.y, rect.width, rect.height].map(value => Math.round(value * 100) / 100));
  }
  const key = JSON.stringify(boxes);
  const prior = window.__offerpilotScreenshotPaint;
  const frames = prior?.key === key ? prior.frames + 1 : 1;
  window.__offerpilotScreenshotPaint = { key, frames };
  return frames >= 3;
}

// Runs in the actual renderer; keep this function closure-free for Playwright.
export function measureScreenGeometry() {
      const controls = [...document.querySelectorAll('button, input, textarea, select, [role="button"]')];
      let haruCoveredControls = 0;
      for (const element of controls) {
        if (element.closest('[aria-label="Haru 助手"], [aria-hidden="true"], [inert]') || element.disabled || !element.getClientRects().length) continue;
        const style = getComputedStyle(element);
        if (style.visibility !== 'visible' || style.opacity === '0') continue;
        const rect = element.getBoundingClientRect();
        const x = rect.left + rect.width / 2; const y = rect.top + rect.height / 2;
        if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) continue;
        // A scrolled child still has a box but its center may be outside a
        // clipping ancestor. Such a point is not a visible interaction target.
        let clipped = false;
        for (let ancestor = element.parentElement; ancestor; ancestor = ancestor.parentElement) {
          const a = ancestor.getBoundingClientRect(); const css = getComputedStyle(ancestor);
          if ((/(auto|scroll|hidden|clip)/.test(css.overflowX) && (x < a.left || x >= a.right))
            || (/(auto|scroll|hidden|clip)/.test(css.overflowY) && (y < a.top || y >= a.bottom))) { clipped = true; break; }
        }
        if (clipped) continue;
        const top = document.elementFromPoint(x, y);
        // An explicitly opened context menu is expected to overlay the page.
        if (top?.closest('[aria-label="Haru 助手"]') && !top.closest('[role="menu"]')) haruCoveredControls++;
      }
      return { width: window.innerWidth, height: window.innerHeight,
        documentWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
        theme: document.documentElement.dataset.theme || 'unknown', haruCoveredControls };
    }

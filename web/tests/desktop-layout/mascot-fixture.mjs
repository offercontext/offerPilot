import { createTaskPanelFixture, TASK_PANEL_ORIGIN } from './task-panel-fixture.mjs';

/** Explicit opt-in only for named-character layout cases. Production defaults
 * stay off; this page needs neither presence nor lifecycle/settings writes. */
export function createMascotFixture() {
  const fixture = createTaskPanelFixture();
  return {
    handle(method, url) {
      const parsed = new URL(url, TASK_PANEL_ORIGIN);
      if (method !== 'GET' || !['/api/companion', '/api/companion/persona'].includes(parsed.pathname)) return undefined;
      return fixture.handle(method, url);
    },
  };
}

export async function installMascotFixture(page) {
  const fixture = createMascotFixture();
  const unexpectedRequests = [];
  await page.route('**/api/**', async route => {
    const request = route.request();
    const response = fixture.handle(request.method(), request.url());
    if (!response) unexpectedRequests.push(`${request.method()} ${new URL(request.url()).pathname}`);
    // Never fall through to Vite's live backend proxy, including rejected reads.
    await route.fulfill({ status: response?.status ?? 403, contentType: 'application/json',
      body: JSON.stringify(response?.data ?? {}) });
  });
  return unexpectedRequests;
}

import { demand } from './contract.mjs';

// Setup only: the app has already cached the pristine profile before the API
// seeds it. Reload normally before any provider token/configuration or arm.
// Never mutate React Query, replay a task, or refresh an in-flight conversation.
export async function refreshSyntheticProfile(page, broker, fixture, deadlineMs) {
  const before = broker.snapshot();
  demand(before.sentRequests === 0 && Array.isArray(before.requests) && before.requests.length === 0
    && before.active === false && before.closed === false, 'SYNTHETIC_SETUP_FAILED');
  const origin = new URL(page.url());
  demand(origin.protocol === 'http:' && origin.hostname === '127.0.0.1' && !origin.username && !origin.password,
    'PRODUCT_ORIGIN_INVALID');
  const expected = [
    ['/api/applications', fixture.applicationId], ['/api/application-events', fixture.eventId],
    ['/api/resumes', fixture.resumeId], ['/api/offers', fixture.offerId],
  ];
  demand(expected.every(([, id]) => Number.isSafeInteger(id) && id > 0), 'SYNTHETIC_PROFILE_INVALID');
  demand(Number.isFinite(deadlineMs) && deadlineMs > Date.now(), 'SESSION_DEADLINE');
  const phaseDeadline = Math.min(Date.now() + 30000, deadlineMs);
  const remaining = () => {
    const left = phaseDeadline - Date.now();
    demand(left > 0, 'SYNTHETIC_SETUP_FAILED');
    return left;
  };
  const bounded = async promise => {
    // Attach a rejection observer even when the shared deadline already passed.
    promise.catch(() => undefined);
    let timer;
    try { return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Object.assign(new Error('SYNTHETIC_SETUP_FAILED'),
        { safeCode: 'SYNTHETIC_SETUP_FAILED' })), remaining());
    })]); } finally { clearTimeout(timer); }
  };
  let navigated = false, invalidObservation = false;
  const freshRequests = new Set();
  const onNavigation = frame => { try { if (frame === page.mainFrame()) navigated = true; } catch { invalidObservation = true; } };
  const onRequest = request => {
    try {
      if (!navigated || request.frame() !== page.mainFrame() || request.method() !== 'GET') return;
      const url = new URL(request.url());
      if (url.origin === origin.origin && !url.search && expected.some(([path]) => path === url.pathname)) freshRequests.add(request);
    } catch { invalidObservation = true; }
  };
  page.on('framenavigated', onNavigation); page.on('request', onRequest);
  const waiters = expected.map(async ([path, id]) => {
    const response = await bounded(page.waitForResponse(response => freshRequests.has(response.request()) &&
      new URL(response.url()).pathname === path, { timeout: remaining() }));
    demand(response.status() === 200, 'SYNTHETIC_SETUP_FAILED');
    const rows = await bounded(response.json());
    demand(Array.isArray(rows) && rows.length === 1 && rows[0]?.id === id, 'SYNTHETIC_PROFILE_INVALID');
  });
  // Observe every rejection even if the navigation itself fails.
  for (const waiter of waiters) waiter.catch(() => undefined);
  try {
    await bounded(page.reload({ waitUntil: 'domcontentloaded', timeout: remaining() }));
    await Promise.all(waiters);
    demand(navigated && !invalidObservation, 'SYNTHETIC_SETUP_FAILED');
    await bounded(page.getByRole('navigation', { name: '主导航', exact: true })
      .waitFor({ state: 'visible', timeout: remaining() }));
    remaining();
    demand(!invalidObservation, 'SYNTHETIC_SETUP_FAILED');
    const after = broker.snapshot();
    demand(after.sentRequests === 0 && after.requests.length === 0 && !after.active && !after.closed,
      'SYNTHETIC_SETUP_FAILED');
  } catch (error) {
    demand(false, error?.safeCode === 'SYNTHETIC_PROFILE_INVALID' ? 'SYNTHETIC_PROFILE_INVALID' : 'SYNTHETIC_SETUP_FAILED');
  } finally { page.off('framenavigated', onNavigation); page.off('request', onRequest); }
}

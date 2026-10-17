import { demand } from './contract.mjs';
const routes = [
  ['GET', /^\/api\/applications$/], ['POST', /^\/api\/applications$/],
  ['POST', /^\/api\/applications\/[1-9]\d*\/job-description\/versions$/],
  ['POST', /^\/api\/application-events$/], ['POST', /^\/api\/resumes$/],
  ['PATCH', /^\/api\/resumes\/[1-9]\d*$/], ['GET', /^\/api\/resumes\/[1-9]\d*$/], ['POST', /^\/api\/offers$/],
];
export function validateSyntheticRoute(route, method) {
  demand(routes.some(([verb, pattern]) => method === verb && pattern.test(route)), 'SYNTHETIC_API_ROUTE_DENIED');
}
export function syntheticApi(page) {
  return async (route, { method = 'GET', body } = {}) => {
    validateSyntheticRoute(route, method);
    const response = await page.evaluate(async ({ route, method, body }) => {
      const result = await fetch(route, { method, headers: { 'Content-Type': 'application/json' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (!result.ok) return { ok: false };
      return { ok: true, value: await result.json() };
    }, { route, method, body });
    demand(response.ok, 'SYNTHETIC_SETUP_FAILED');
    return response.value;
  };
}

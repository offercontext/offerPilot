import path from 'node:path';
import { ORIGIN } from './fixture-api.mjs';

/** Pure guard shared by the real browser router and offline negative tests. */
export function classifyRequest(method, rawUrl, dist) {
  let url;
  try { url = new URL(rawUrl); } catch { return { blocked: 'invalid_url' }; }
  if (url.origin !== ORIGIN || url.username || url.password) return { blocked: 'external_origin' };
  if (url.pathname.startsWith('/api/')) return { api: true };
  if (method !== 'GET') return { blocked: 'non_get_asset' };
  let relative;
  try { relative = decodeURIComponent(url.pathname); } catch { return { blocked: 'invalid_asset_encoding' }; }
  if (relative.includes('\\') || relative.includes('\0') || relative.split('/').includes('..')) return { blocked: 'unsafe_asset_path' };
  const filename = path.resolve(dist, relative === '/' ? 'index.html' : relative.slice(1));
  if (!filename.startsWith(path.resolve(dist) + path.sep)) return { blocked: 'asset_escape' };
  return { filename };
}

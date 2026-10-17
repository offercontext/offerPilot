import { PIN, demand } from './contract.mjs';
export function githubReader(token, fetchImpl = fetch) {
  demand(typeof token === 'string' && token.length > 0, 'GITHUB_READ_TOKEN_MISSING');
  return async suffix => {
    demand(typeof suffix === 'string' && !suffix.startsWith('/') && !suffix.includes('://') && !suffix.includes('\\') && !suffix.split(/[/?#]/).some(part => part === '..' || part === '.'), 'GITHUB_READ_PATH_DENIED');
    let response;
    try {
      response = await fetchImpl(`https://api.github.com/repos/${PIN.repository}/${suffix}`, {
        method: 'GET', redirect: 'error', signal: AbortSignal.timeout(20000),
        headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}`, 'X-GitHub-Api-Version': '2022-11-28' },
      });
    } catch { demand(false, 'GITHUB_READ_UNAVAILABLE'); }
    demand(response.ok, 'GITHUB_READ_UNAVAILABLE');
    demand(!response.headers.get('link')?.includes('rel="next"'), 'GITHUB_READ_INCOMPLETE');
    try { return await response.json(); } catch { demand(false, 'GITHUB_READ_INVALID'); }
  };
}

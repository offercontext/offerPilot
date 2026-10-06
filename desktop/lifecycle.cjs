'use strict';
const http = require('node:http');
const { createInterface } = require('node:readline');

const TOKEN_HEADER = 'X-OfferPilot-Desktop-Token';
function isLocalOrigin(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' && url.hostname === '127.0.0.1'
      && Number(url.port) > 0 && Number(url.port) <= 65535
      && !url.username && !url.password && url.pathname === '/' && !url.search && !url.hash;
  } catch { return false; }
}
function isSameOrigin(value, origin) {
  try { return new URL(value).origin === origin; } catch { return false; }
}
function waitForReady(child, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const lines = createInterface({ input: child.stdout });
    const timer = setTimeout(() => finish(new Error('Backend startup timed out.')), timeoutMs);
    const onExit = (code) => finish(new Error(`Backend exited before startup (${code}).`));
    const onError = () => finish(new Error('Could not start the bundled backend.'));
    function finish(error, ready) {
      clearTimeout(timer);
      lines.close();
      child.off('exit', onExit);
      child.off('error', onError);
      if (error) reject(error); else resolve(ready);
    }
    child.once('exit', onExit);
    child.once('error', onError);
    lines.on('line', (line) => {
      if (line.length > 8192) return;
      let message;
      try { message = JSON.parse(line); } catch { return; }
      if (message.type === 'offerpilot.desktop.error') {
        finish(new Error('Backend startup failed. Check the desktop log for details.'));
      } else if (message.type === 'offerpilot.desktop.ready') {
        if (message.protocol !== 1 || message.pid !== child.pid || !isLocalOrigin(message.origin)) {
          finish(new Error('Invalid backend readiness response.'));
        } else finish(null, message);
      }
    });
  });
}
function checkHealth(origin, token) {
  return new Promise((resolve, reject) => {
    const request = http.get(`${origin}/api/health`, {
      headers: { [TOKEN_HEADER]: token }, timeout: 5000,
    }, (response) => {
      response.resume();
      response.on('end', () => response.statusCode === 200 ? resolve() : reject(new Error('Backend health check failed.')));
    });
    request.on('timeout', () => request.destroy(new Error('Backend health check timed out.')));
    request.on('error', reject);
  });
}
async function stopBackend(child, graceMs = 12000) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve) => {
    const timer = setTimeout(() => { child.kill(); }, graceMs);
    const deadline = setTimeout(() => { cleanup(); resolve(); }, graceMs + 3000);
    function cleanup() { clearTimeout(timer); clearTimeout(deadline); }
    child.once('exit', () => { cleanup(); resolve(); });
    // EOF is the cross-platform graceful shutdown protocol, including Windows.
    child.stdin.on('error', () => {});
    child.stdin.end();
  });
}
module.exports = { TOKEN_HEADER, isLocalOrigin, isSameOrigin, waitForReady, checkHealth, stopBackend };

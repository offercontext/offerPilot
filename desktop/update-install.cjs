'use strict';
// Unlike ordinary app exit, an update must never proceed after a shutdown
// timeout or kill the writer to make an unsafe data snapshot look successful.
function stopBackendForUpdate(child, timeoutMs = 15000) {
  if (!child) return Promise.reject(new Error('Backend process is unknown'));
  if (child.exitCode !== null || child.signalCode !== null) return child.exitCode === 0 && child.signalCode === null
    ? Promise.resolve() : Promise.reject(new Error('Backend did not stop cleanly'));
  return new Promise((resolve, reject) => {
    const cleanup = () => { clearTimeout(timer); child.removeListener('exit', exit); };
    const exit = (code, signal) => { cleanup(); if (code === 0 && (signal === null || signal === undefined)) resolve(); else reject(new Error('Backend did not stop cleanly')); };
    const timer = setTimeout(() => { cleanup(); reject(new Error('Backend shutdown timed out')); }, timeoutMs);
    child.once('exit', exit);
    try { child.stdin.end(); } catch (error) { cleanup(); reject(error); }
  });
}
module.exports = { stopBackendForUpdate };

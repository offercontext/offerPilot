// Teardown is containment, not acceptance. It must remain available after a
// safety assertion fails and must never manufacture a passed checkpoint.
export async function bounded(operation, timeout) {
  let timer;
  try {
    return await Promise.race([Promise.resolve().then(operation), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('shutdown-timeout')), timeout);
    })]);
  } finally { clearTimeout(timer); }
}

export async function requestProductionQuit(app) {
  return bounded(() => app.evaluate(({ Menu }) => {
    const matches = Menu.getApplicationMenu().items.flatMap(item => item.submenu?.items || [])
      .filter(item => ['退出并停止本地服务', 'Quit and Stop Local Service'].includes(item.label));
    if (matches.length !== 1 || matches[0].enabled === false || typeof matches[0].click !== 'function') throw new Error('explicit-quit-not-found');
    setTimeout(() => matches[0].click(matches[0], undefined, {}), 0);
    return true;
  }), 10000);
}

export async function containFailedLaunch({ requestQuit, waitExited, forceOwned }) {
  const result = { purpose: 'failure-containment-only', productionQuitRequested: false, forcedCleanupAttempted: false };
  try { result.productionQuitRequested = await requestQuit() === true; } catch { /* Try identity-bound fallback. */ }
  try { Object.assign(result, await waitExited()); return result; } catch { /* Never count a timeout as exit. */ }
  result.forcedCleanupAttempted = true;
  try { Object.assign(result, await forceOwned()); }
  catch { result.forceCleanupUnconfirmed = true; }
  try { Object.assign(result, await waitExited()); }
  catch { result.allInstalledProcessesExited = false; result.backendPortClosed = null; }
  return result;
}

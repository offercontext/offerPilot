// Read-only acceptance polling. Await the result, not the Promise's truthiness.
export async function waitForMailState(read, predicate, { timeoutMs = 30000, pollMs = 100 } = {}) {
  if (!Number.isFinite(timeoutMs) || !Number.isFinite(pollMs) || !(timeoutMs > 0) || !(pollMs > 0)) throw new Error('Polling limits must be positive');
  const deadline = performance.now() + timeoutMs;
  const timeout = () => new Error('Synthetic acceptance state deadline exceeded');
  while (performance.now() < deadline) {
    let timer;
    try {
      const result = await Promise.race([
        (async () => { const value = await read(); return { value, ready: await predicate(value) }; })(),
        new Promise((_, reject) => { timer = setTimeout(() => reject(timeout()), Math.max(1, deadline - performance.now())); }),
      ]);
      if (result.ready) return result.value;
    } finally { clearTimeout(timer); }
    const remaining = deadline - performance.now();
    if (remaining <= 0) throw timeout();
    await new Promise(resolve => setTimeout(resolve, Math.min(pollMs, remaining)));
  }
  throw timeout();
}

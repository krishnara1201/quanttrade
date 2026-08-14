export async function pollUntil(fetchFn, isDone, { intervalMs = 2000, timeoutMs = 720000 } = {}) {
  const start = Date.now();
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const value = await fetchFn();
    if (isDone(value)) return value;
    if (Date.now() - start > timeoutMs) {
      throw new Error('Timed out waiting for the task to finish');
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

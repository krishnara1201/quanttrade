import { describe, it, expect, vi } from 'vitest';
import { pollUntil } from './pollUntil.js';

describe('pollUntil', () => {
  it('returns immediately once isDone is satisfied on the first call', async () => {
    const fetchFn = vi.fn().mockResolvedValue({ status: 'success' });
    const isDone = (value) => value.status === 'success';

    const result = await pollUntil(fetchFn, isDone, { intervalMs: 0 });

    expect(result).toEqual({ status: 'success' });
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it('polls repeatedly until isDone is satisfied', async () => {
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce({ status: 'pending' })
      .mockResolvedValueOnce({ status: 'running' })
      .mockResolvedValueOnce({ status: 'success' });
    const isDone = (value) => value.status === 'success';

    const result = await pollUntil(fetchFn, isDone, { intervalMs: 0 });

    expect(result).toEqual({ status: 'success' });
    expect(fetchFn).toHaveBeenCalledTimes(3);
  });

  it('throws once elapsed time exceeds timeoutMs without isDone ever being true', async () => {
    let now = 0;
    const realDateNow = Date.now;
    Date.now = () => now;

    try {
      const fetchFn = vi.fn().mockImplementation(async () => {
        now += 100;
        return { status: 'pending' };
      });
      const isDone = () => false;

      await expect(
        pollUntil(fetchFn, isDone, { intervalMs: 0, timeoutMs: 250 })
      ).rejects.toThrow('Timed out waiting for the task to finish');
    } finally {
      Date.now = realDateNow;
    }
  });
});

import { describe, it, expect, vi, beforeEach } from 'vitest';
import client, { setAuthToken, setRefreshHandler, handleResponseError } from './client.js';

describe('setAuthToken', () => {
  it('attaches a Bearer Authorization header when given a token', () => {
    setAuthToken('abc123');
    expect(client.defaults.headers.common.Authorization).toBe('Bearer abc123');
  });

  it('removes the Authorization header when called with null', () => {
    setAuthToken('abc123');
    setAuthToken(null);
    expect(client.defaults.headers.common.Authorization).toBeUndefined();
  });

  it('removes the Authorization header when called with an empty string', () => {
    setAuthToken('abc123');
    setAuthToken('');
    expect(client.defaults.headers.common.Authorization).toBeUndefined();
  });
});

describe('client config', () => {
  it('sends credentials (cookies) with every request', () => {
    expect(client.defaults.withCredentials).toBe(true);
  });
});

describe('handleResponseError (401 refresh-and-retry)', () => {
  beforeEach(() => {
    setRefreshHandler(null);
  });

  it('retries the original request with a new token when the refresh handler succeeds', async () => {
    const refreshHandler = vi.fn().mockResolvedValue('new-token');
    setRefreshHandler(refreshHandler);

    const retried = { data: 'ok' };
    vi.spyOn(client, 'request').mockResolvedValue(retried);

    const error = {
      config: { url: '/api/projects', headers: {} },
      response: { status: 401 },
    };

    const result = await handleResponseError(error);

    expect(refreshHandler).toHaveBeenCalledTimes(1);
    expect(result).toBe(retried);
    expect(error.config.headers.Authorization).toBe('Bearer new-token');
    expect(error.config._retriedAfterRefresh).toBe(true);

    client.request.mockRestore();
  });

  it('rejects without retrying when the failing request is itself an auth-route call', async () => {
    const refreshHandler = vi.fn().mockResolvedValue('new-token');
    setRefreshHandler(refreshHandler);

    const error = {
      config: { url: '/api/auth/refresh', headers: {} },
      response: { status: 401 },
    };

    await expect(handleResponseError(error)).rejects.toBe(error);
    expect(refreshHandler).not.toHaveBeenCalled();
  });

  it('rejects without retrying when no refresh handler is registered', async () => {
    const error = {
      config: { url: '/api/projects', headers: {} },
      response: { status: 401 },
    };

    await expect(handleResponseError(error)).rejects.toBe(error);
  });

  it('rejects when the refresh handler resolves to null (refresh failed)', async () => {
    const refreshHandler = vi.fn().mockResolvedValue(null);
    setRefreshHandler(refreshHandler);
    const error = {
      config: { url: '/api/projects', headers: {} },
      response: { status: 401 },
    };

    await expect(handleResponseError(error)).rejects.toBe(error);
    expect(refreshHandler).toHaveBeenCalledTimes(1);
  });

  it('rejects a non-401 error without touching the refresh handler', async () => {
    const refreshHandler = vi.fn();
    setRefreshHandler(refreshHandler);
    const error = {
      config: { url: '/api/projects', headers: {} },
      response: { status: 500 },
    };

    await expect(handleResponseError(error)).rejects.toBe(error);
    expect(refreshHandler).not.toHaveBeenCalled();
  });

  it('does not retry twice for the same request', async () => {
    const refreshHandler = vi.fn().mockResolvedValue('new-token');
    setRefreshHandler(refreshHandler);
    const error = {
      config: { url: '/api/projects', headers: {}, _retriedAfterRefresh: true },
      response: { status: 401 },
    };

    await expect(handleResponseError(error)).rejects.toBe(error);
    expect(refreshHandler).not.toHaveBeenCalled();
  });
});

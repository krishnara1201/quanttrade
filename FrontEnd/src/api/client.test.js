import { describe, it, expect } from 'vitest';
import client, { setAuthToken } from './client.js';

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

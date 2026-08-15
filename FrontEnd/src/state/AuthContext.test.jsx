import React, { useEffect } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AuthProvider, useAuth } from './AuthContext.jsx';
import client from '../api/client.js';
import * as authApi from '../api/auth.js';

vi.mock('../api/auth.js');

function fakeJwt(payload) {
  const header = btoa(JSON.stringify({ alg: 'none' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.signature`;
}

function TestConsumer() {
  const { isAuthenticated, profile, error, login, register, logout } = useAuth();
  return (
    <div>
      <div data-testid="authed">{String(isAuthenticated)}</div>
      <div data-testid="email">{profile?.email ?? ''}</div>
      <div data-testid="error">{error}</div>
      <button onClick={() => login('trader@desk.com', 'hunter2')}>login</button>
      <button onClick={() => register({ name: 'Ada', email: 'ada@desk.com', password: 'pw' })}>
        register
      </button>
      <button onClick={logout}>logout</button>
    </div>
  );
}

describe('AuthProvider', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('attaches the Authorization header synchronously during render, before any child mount effect fires', () => {
    const token = fakeJwt({ sub: 'trader@desk.com', user_id: 7 });
    localStorage.setItem('quanttrade.jwt', token);

    const seenHeaders = [];
    function Probe() {
      // Runs as a child mount-time effect, mirroring pages like ProjectsPage
      // that fire their initial data fetch in their own useEffect(() => {...}, []).
      // React fires child effects before parent effects, so this only sees the
      // header if AuthProvider attached it synchronously during render.
      useEffect(() => {
        seenHeaders.push(client.defaults.headers.common.Authorization);
      }, []);
      return null;
    }

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    );

    expect(seenHeaders).toEqual([`Bearer ${token}`]);
  });

  it('has no Authorization header when there is no stored token', () => {
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    expect(client.defaults.headers.common.Authorization).toBeUndefined();
  });

  it('logs in successfully, decodes the profile, and persists the token', async () => {
    authApi.login.mockResolvedValue({ access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1 }) });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await user.click(screen.getByText('login'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
    expect(screen.getByTestId('email')).toHaveTextContent('trader@desk.com');
    expect(localStorage.getItem('quanttrade.jwt')).toBeTruthy();
    expect(client.defaults.headers.common.Authorization).toMatch(/^Bearer /);
  });

  it('surfaces the backend error message and stays unauthenticated on failed login', async () => {
    authApi.login.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await user.click(screen.getByText('login'));

    await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent('Invalid credentials'));
    expect(screen.getByTestId('authed')).toHaveTextContent('false');
    expect(localStorage.getItem('quanttrade.jwt')).toBeNull();
  });

  it('registers then logs in automatically on success', async () => {
    authApi.register.mockResolvedValue({ id: 1 });
    authApi.login.mockResolvedValue({ access_token: fakeJwt({ sub: 'ada@desk.com', user_id: 2 }) });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await user.click(screen.getByText('register'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
    expect(authApi.register).toHaveBeenCalledWith({ name: 'Ada', email: 'ada@desk.com', password: 'pw' });
    expect(authApi.login).toHaveBeenCalledWith('ada@desk.com', 'pw');
  });

  it('clears the token, profile, and Authorization header on logout', async () => {
    authApi.login.mockResolvedValue({ access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1 }) });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await user.click(screen.getByText('login'));
    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));

    await user.click(screen.getByText('logout'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('false'));
    expect(localStorage.getItem('quanttrade.jwt')).toBeNull();
    expect(client.defaults.headers.common.Authorization).toBeUndefined();
  });
});

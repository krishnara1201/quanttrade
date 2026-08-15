import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
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

function futureExp(seconds) {
  return Math.floor(Date.now() / 1000) + seconds;
}

function TestConsumer() {
  const { isAuthenticated, bootstrapping, profile, error, login, register, logout } = useAuth();
  return (
    <div>
      <div data-testid="bootstrapping">{String(bootstrapping)}</div>
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
    vi.clearAllMocks();
    authApi.refresh.mockRejectedValue(new Error('no session'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts bootstrapping true and attempts a silent refresh on mount', async () => {
    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    expect(screen.getByTestId('bootstrapping')).toHaveTextContent('true');
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));
    expect(authApi.refresh).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('authed')).toHaveTextContent('false');
  });

  it('rehydrates an authenticated session when the silent refresh succeeds', async () => {
    authApi.refresh.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 7, exp: futureExp(900) }),
    });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));
    expect(screen.getByTestId('authed')).toHaveTextContent('true');
    expect(screen.getByTestId('email')).toHaveTextContent('trader@desk.com');
    expect(client.defaults.headers.common.Authorization).toMatch(/^Bearer /);
  });

  it('logs in successfully and decodes the profile, without touching localStorage', async () => {
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1, exp: futureExp(900) }),
    });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));

    await user.click(screen.getByText('login'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
    expect(screen.getByTestId('email')).toHaveTextContent('trader@desk.com');
    expect(client.defaults.headers.common.Authorization).toMatch(/^Bearer /);
    expect(localStorage.getItem('quanttrade.jwt')).toBeNull();
  });

  it('surfaces the backend error message and stays unauthenticated on failed login', async () => {
    authApi.login.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));

    await user.click(screen.getByText('login'));

    await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent('Invalid credentials'));
    expect(screen.getByTestId('authed')).toHaveTextContent('false');
  });

  it('registers then logs in automatically on success', async () => {
    authApi.register.mockResolvedValue({ id: 1 });
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'ada@desk.com', user_id: 2, exp: futureExp(900) }),
    });
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));

    await user.click(screen.getByText('register'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));
    expect(authApi.register).toHaveBeenCalledWith({ name: 'Ada', email: 'ada@desk.com', password: 'pw' });
    expect(authApi.login).toHaveBeenCalledWith('ada@desk.com', 'pw');
  });

  it('calls the backend logout endpoint and clears the session', async () => {
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1, exp: futureExp(900) }),
    });
    authApi.logout.mockResolvedValue();
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));

    await user.click(screen.getByText('login'));
    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));

    await user.click(screen.getByText('logout'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('false'));
    expect(authApi.logout).toHaveBeenCalledTimes(1);
    expect(client.defaults.headers.common.Authorization).toBeUndefined();
  });

  it('clears the session locally even if the backend logout call fails', async () => {
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1, exp: futureExp(900) }),
    });
    authApi.logout.mockRejectedValue(new Error('network error'));
    const user = userEvent.setup();

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await waitFor(() => expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false'));

    await user.click(screen.getByText('login'));
    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('true'));

    await user.click(screen.getByText('logout'));

    await waitFor(() => expect(screen.getByTestId('authed')).toHaveTextContent('false'));
  });

  it('proactively refreshes the token before it expires', async () => {
    vi.useFakeTimers();
    authApi.refresh.mockRejectedValueOnce(new Error('no session')); // the bootstrap call
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1, exp: futureExp(100) }),
    });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );
    await act(async () => {
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.getByTestId('bootstrapping')).toHaveTextContent('false');

    authApi.refresh.mockResolvedValueOnce({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1, exp: futureExp(1000) }),
    });

    await act(async () => {
      fireEvent.click(screen.getByText('login'));
      await vi.runOnlyPendingTimersAsync();
    });
    expect(screen.getByTestId('authed')).toHaveTextContent('true');
    expect(authApi.refresh).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(81_000); // > 80% of the 100s-lifetime token
    });

    expect(authApi.refresh).toHaveBeenCalledTimes(2);
  });
});

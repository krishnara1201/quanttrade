import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import AuthPage from './AuthPage.jsx';
import { AuthProvider } from '../state/AuthContext.jsx';
import * as authApi from '../api/auth.js';

vi.mock('../api/auth.js');

function renderAuthPage() {
  return render(
    <MemoryRouter initialEntries={['/auth']}>
      <AuthProvider>
        <Routes>
          <Route path="/auth" element={<AuthPage />} />
          <Route path="/projects" element={<div>projects landing</div>} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>
  );
}

function submitButton(container) {
  return container.querySelector('button[type="submit"]');
}

function fakeJwt(payload) {
  const header = btoa(JSON.stringify({ alg: 'none' }));
  const body = btoa(JSON.stringify(payload));
  return `${header}.${body}.signature`;
}

describe('AuthPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authApi.refresh.mockRejectedValue(new Error('no session'));
  });

  it('logs in and navigates to /projects on success', async () => {
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'trader@desk.com', user_id: 1 }),
    });
    const user = userEvent.setup();

    const { container } = renderAuthPage();

    await user.type(screen.getByPlaceholderText('trader@desk.com'), 'trader@desk.com');
    await user.type(screen.getByPlaceholderText('••••••••'), 'hunter2');
    await user.click(submitButton(container));

    await waitFor(() => expect(screen.getByText('projects landing')).toBeInTheDocument());
    expect(authApi.login).toHaveBeenCalledWith('trader@desk.com', 'hunter2');
  });

  it('shows the backend error and stays on the form when login fails', async () => {
    authApi.login.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } });
    const user = userEvent.setup();

    const { container } = renderAuthPage();

    await user.type(screen.getByPlaceholderText('trader@desk.com'), 'trader@desk.com');
    await user.type(screen.getByPlaceholderText('••••••••'), 'wrong');
    await user.click(submitButton(container));

    await waitFor(() => expect(screen.getByText('Invalid credentials')).toBeInTheDocument());
    expect(screen.queryByText('projects landing')).not.toBeInTheDocument();
  });

  it('switches to the register tab and submits name/email/password', async () => {
    authApi.register.mockResolvedValue({ id: 1 });
    authApi.login.mockResolvedValue({
      access_token: fakeJwt({ sub: 'ada@desk.com', user_id: 2 }),
    });
    const user = userEvent.setup();

    const { container } = renderAuthPage();

    await user.click(screen.getByRole('button', { name: 'Register' }));
    await user.type(screen.getByPlaceholderText('Ada Lovelace'), 'Ada Lovelace');
    await user.type(screen.getByPlaceholderText('trader@desk.com'), 'ada@desk.com');
    await user.type(screen.getByPlaceholderText('••••••••'), 'pw123456');
    await user.click(submitButton(container));

    await waitFor(() =>
      expect(authApi.register).toHaveBeenCalledWith({
        name: 'Ada Lovelace',
        email: 'ada@desk.com',
        password: 'pw123456',
      })
    );
    await waitFor(() => expect(screen.getByText('projects landing')).toBeInTheDocument());
  });
});

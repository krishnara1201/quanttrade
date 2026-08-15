import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import ProtectedRoute from './ProtectedRoute.jsx';
import { useAuth } from '../state/AuthContext.jsx';

vi.mock('../state/AuthContext.jsx', () => ({
  useAuth: vi.fn(),
}));

function renderAt(path) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/auth" element={<div>auth page</div>} />
        <Route
          path="/projects"
          element={(
            <ProtectedRoute>
              <div>projects page</div>
            </ProtectedRoute>
          )}
        />
      </Routes>
    </MemoryRouter>
  );
}

describe('ProtectedRoute', () => {
  it('shows a loading state while the session is still bootstrapping, without redirecting', () => {
    useAuth.mockReturnValue({ isAuthenticated: false, bootstrapping: true });

    renderAt('/projects');

    expect(screen.queryByText('auth page')).not.toBeInTheDocument();
    expect(screen.queryByText('projects page')).not.toBeInTheDocument();
  });

  it('redirects to /auth once bootstrapped and not authenticated', () => {
    useAuth.mockReturnValue({ isAuthenticated: false, bootstrapping: false });

    renderAt('/projects');

    expect(screen.getByText('auth page')).toBeInTheDocument();
    expect(screen.queryByText('projects page')).not.toBeInTheDocument();
  });

  it('renders the protected content once bootstrapped and authenticated', () => {
    useAuth.mockReturnValue({ isAuthenticated: true, bootstrapping: false });

    renderAt('/projects');

    expect(screen.getByText('projects page')).toBeInTheDocument();
    expect(screen.queryByText('auth page')).not.toBeInTheDocument();
  });
});

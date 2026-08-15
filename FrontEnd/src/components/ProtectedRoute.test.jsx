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
  it('redirects to /auth when the user is not authenticated', () => {
    useAuth.mockReturnValue({ isAuthenticated: false });

    renderAt('/projects');

    expect(screen.getByText('auth page')).toBeInTheDocument();
    expect(screen.queryByText('projects page')).not.toBeInTheDocument();
  });

  it('renders the protected content when the user is authenticated', () => {
    useAuth.mockReturnValue({ isAuthenticated: true });

    renderAt('/projects');

    expect(screen.getByText('projects page')).toBeInTheDocument();
    expect(screen.queryByText('auth page')).not.toBeInTheDocument();
  });
});

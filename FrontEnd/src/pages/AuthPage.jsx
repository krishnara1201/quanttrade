import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../state/AuthContext.jsx';

export default function AuthPage() {
  const { login, register, loading, error, isAuthenticated } = useAuth();
  const [mode, setMode] = useState('login');
  const [form, setForm] = useState({ name: '', email: '', password: '' });
  const navigate = useNavigate();
  const location = useLocation();

  const handleSubmit = async (e) => {
    e.preventDefault();
    const success = mode === 'login'
      ? await login(form.email, form.password)
      : await register(form);

    if (success) {
      const redirectTo = location.state?.from?.pathname || '/projects';
      navigate(redirectTo, { replace: true });
    }
  };

  const updateField = (key, value) => setForm((prev) => ({ ...prev, [key]: value }));

  if (isAuthenticated) {
    return (
      <div className="page narrow">
        <div className="card">
          <h2>You are signed in.</h2>
          <p>Continue to your projects.</p>
          <button className="primary-btn" onClick={() => navigate('/projects')}>Go to projects</button>
        </div>
      </div>
    );
  }

  return (
    <div className="page narrow">
      <div className="card">
        <div className="tabs">
          <button className={mode === 'login' ? 'tab active' : 'tab'} onClick={() => setMode('login')}>Login</button>
          <button className={mode === 'register' ? 'tab active' : 'tab'} onClick={() => setMode('register')}>Register</button>
        </div>
        <form onSubmit={handleSubmit} className="stack">
          {mode === 'register' && (
            <label className="field">
              <span>Name</span>
              <input required value={form.name} onChange={(e) => updateField('name', e.target.value)} placeholder="Ada Lovelace" />
            </label>
          )}
          <label className="field">
            <span>Email</span>
            <input type="email" required value={form.email} onChange={(e) => updateField('email', e.target.value)} placeholder="trader@desk.com" />
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" required value={form.password} onChange={(e) => updateField('password', e.target.value)} placeholder="••••••••" />
          </label>
          {error && <div className="error-box">{error}</div>}
          <button className="primary-btn" type="submit" disabled={loading}>
            {loading ? 'Working...' : mode === 'login' ? 'Login' : 'Register'}
          </button>
        </form>
      </div>
    </div>
  );
}

import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import * as authApi from '../api/auth.js';
import { setAuthToken } from '../api/client.js';

const AuthContext = createContext(null);

const storageKey = 'quanttrade.jwt';

function decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    const decoded = JSON.parse(atob(payload));
    return { email: decoded.sub, userId: decoded.user_id };
  } catch (err) {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(storageKey) || '');
  const [profile, setProfile] = useState(() => decodeJwt(localStorage.getItem(storageKey) || '') || null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Applied synchronously during render (not in an effect) so the axios client
  // has the Authorization header attached before any child's mount-time effect
  // (e.g. a page's initial data fetch) can fire a request without it — child
  // effects run before parent effects on mount, so a useEffect here is too late.
  setAuthToken(token || null);

  useEffect(() => {
    if (token) {
      localStorage.setItem(storageKey, token);
      setProfile(decodeJwt(token));
    } else {
      localStorage.removeItem(storageKey);
      setProfile(null);
    }
  }, [token]);

  const login = async (email, password) => {
    setLoading(true);
    setError('');
    try {
      const resp = await authApi.login(email, password);
      setToken(resp.access_token);
      return true;
    } catch (err) {
      setError(err?.response?.data?.detail || err?.response?.data?.error || 'Login failed');
      return false;
    } finally {
      setLoading(false);
    }
  };

  const register = async (payload) => {
    setLoading(true);
    setError('');
    try {
      await authApi.register(payload);
      await login(payload.email, payload.password);
      return true;
    } catch (err) {
      setError(err?.response?.data?.detail || 'Registration failed');
      return false;
    } finally {
      setLoading(false);
    }
  };

  const logout = () => setToken('');

  const value = useMemo(() => ({
    token,
    profile,
    isAuthenticated: Boolean(token),
    loading,
    error,
    login,
    register,
    logout,
  }), [token, profile, loading, error]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

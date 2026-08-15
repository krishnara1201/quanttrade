import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import * as authApi from '../api/auth.js';
import { setAuthToken, setRefreshHandler } from '../api/client.js';

const AuthContext = createContext(null);

const PROACTIVE_REFRESH_RATIO = 0.8;

function decodeJwt(token) {
  try {
    const payload = token.split('.')[1];
    const decoded = JSON.parse(atob(payload));
    return { email: decoded.sub, userId: decoded.user_id, exp: decoded.exp };
  } catch (err) {
    return null;
  }
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState('');
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [bootstrapping, setBootstrapping] = useState(true);
  const [error, setError] = useState('');
  const refreshTimerRef = useRef(null);
  const inFlightRefreshRef = useRef(null);

  // Applied synchronously during render (not in an effect) so the axios
  // client has the Authorization header attached before any child's
  // mount-time effect can fire a request without it — see the
  // child-effects-run-before-parent-effects note this fixed previously.
  // The access token now lives only in this in-memory state (never
  // localStorage); ProtectedRoute gates children on `bootstrapping` so
  // mount-time fetches never race the initial silent refresh below.
  setAuthToken(token || null);

  const applyToken = useCallback((accessToken) => {
    setToken(accessToken);
    setProfile(decodeJwt(accessToken));
  }, []);

  const clearSession = useCallback(() => {
    setToken('');
    setProfile(null);
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  const silentRefresh = useCallback(() => {
    // Single-flight guard: concurrent callers (e.g. several pages' mount-time
    // 401s all hitting the client.js interceptor's refreshHandler at once, or
    // React.StrictMode's synchronous double-invoke of the bootstrap effect)
    // must share one outstanding /api/auth/refresh request rather than each
    // firing their own — a second request racing in with the same
    // not-yet-rotated refresh-token cookie gets flagged as reuse by the
    // backend and revokes every live refresh token for the user. The ref is
    // set synchronously (before any await) so same-tick concurrent calls see
    // it already populated instead of racing to check-then-set.
    if (inFlightRefreshRef.current) {
      return inFlightRefreshRef.current;
    }
    const promise = (async () => {
      try {
        const resp = await authApi.refresh();
        applyToken(resp.access_token);
        return resp.access_token;
      } catch (err) {
        clearSession();
        return null;
      } finally {
        inFlightRefreshRef.current = null;
      }
    })();
    inFlightRefreshRef.current = promise;
    return promise;
  }, [applyToken, clearSession]);

  useEffect(() => {
    setRefreshHandler(silentRefresh);
  }, [silentRefresh]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      await silentRefresh();
      if (!cancelled) setBootstrapping(false);
    })();
    return () => {
      cancelled = true;
    };
    // Runs once on mount only — silentRefresh's identity is stable across
    // renders (see its useCallback deps), so this is not missing a dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (refreshTimerRef.current) {
      clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
    if (!profile?.exp) return undefined;

    const nowSeconds = Date.now() / 1000;
    const lifetimeSeconds = profile.exp - nowSeconds;
    const delayMs = Math.max(lifetimeSeconds * PROACTIVE_REFRESH_RATIO, 0) * 1000;

    refreshTimerRef.current = setTimeout(() => {
      silentRefresh();
    }, delayMs);

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current);
    };
  }, [profile, silentRefresh]);

  const login = async (email, password) => {
    setLoading(true);
    setError('');
    try {
      const resp = await authApi.login(email, password);
      applyToken(resp.access_token);
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

  const logout = async () => {
    try {
      await authApi.logout();
    } catch (err) {
      // Best-effort: clear the local session even if the server call fails
      // (e.g. network error, or the refresh cookie was already invalid).
    }
    clearSession();
  };

  const value = useMemo(() => ({
    token,
    profile,
    isAuthenticated: Boolean(token),
    loading,
    bootstrapping,
    error,
    login,
    register,
    logout,
  }), [token, profile, loading, bootstrapping, error]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}

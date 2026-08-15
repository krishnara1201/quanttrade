import axios from 'axios';

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

const client = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

export function setAuthToken(token) {
  if (token) {
    client.defaults.headers.common.Authorization = `Bearer ${token}`;
  } else {
    delete client.defaults.headers.common.Authorization;
  }
}

// AuthContext registers itself here so the 401 interceptor below can trigger
// a silent token refresh without client.js importing AuthContext (which
// would be circular, since AuthContext already imports client.js).
let refreshHandler = null;
export function setRefreshHandler(handler) {
  refreshHandler = handler;
}

export async function handleResponseError(error) {
  const { config, response } = error;
  const isAuthRoute = typeof config?.url === 'string' && config.url.startsWith('/api/auth/');

  if (response?.status === 401 && !isAuthRoute && !config?._retriedAfterRefresh && refreshHandler) {
    config._retriedAfterRefresh = true;
    const newToken = await refreshHandler();
    if (newToken) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${newToken}`;
      return client.request(config);
    }
  }
  return Promise.reject(error);
}

client.interceptors.response.use((response) => response, handleResponseError);

export default client;

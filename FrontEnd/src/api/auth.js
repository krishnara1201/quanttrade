import client from './client.js';

export async function register({ name, email, password }) {
  const { data } = await client.post('/api/auth/', { name, email, password });
  return data;
}

export async function login(email, password) {
  const body = new URLSearchParams();
  body.append('username', email);
  body.append('password', password);

  const { data } = await client.post('/api/auth/token', body, {
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
  });
  if (!data.access_token) {
    throw new Error('Token not returned');
  }
  return data;
}

import client from './client.js';

export async function fetchStrategies() {
  const { data } = await client.get('/strategies/');
  return data;
}

export async function createStrategy(payload) {
  const { data } = await client.post('/strategies/', payload);
  return data;
}

export async function updateStrategy(id, payload) {
  const { data } = await client.put(`/strategies/${id}`, payload);
  return data;
}

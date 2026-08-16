import client from './client.js';

export async function fetchProjects() {
  const { data } = await client.get('/api/projects/');
  return data;
}

export async function createProject(payload) {
  const { data } = await client.post('/api/projects/', payload);
  return data;
}

export async function deleteProject(id) {
  const { data } = await client.delete(`/api/projects/${id}`);
  return data;
}

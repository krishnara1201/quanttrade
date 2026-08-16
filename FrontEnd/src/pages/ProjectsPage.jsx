import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import * as projectsApi from '../api/projects.js';

export default function ProjectsPage() {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [form, setForm] = useState({ name: '', description: '' });
  const [deletingId, setDeletingId] = useState(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await projectsApi.fetchProjects();
      setProjects(data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to load projects');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleCreate = async (e) => {
    e.preventDefault();
    try {
      const created = await projectsApi.createProject(form);
      setProjects((prev) => [...prev, created]);
      setForm({ name: '', description: '' });
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not create project');
    }
  };

  const handleDelete = async (project) => {
    if (!window.confirm(`Delete project "${project.name}"? This also deletes all of its strategies and backtests. This cannot be undone.`)) {
      return;
    }
    setDeletingId(project.id);
    try {
      await projectsApi.deleteProject(project.id);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not delete project');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <p className="pill">Authenticated zone</p>
          <h1>Your projects</h1>
          <p className="lede">Projects belong to the authenticated user (per /api/projects backend constraint).</p>
        </div>
      </div>

      <div className="layout two-cols">
        <div className="card">
          <h3>Create a project</h3>
          <form className="stack" onSubmit={handleCreate}>
            <label className="field">
              <span>Name</span>
              <input value={form.name} required onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Momentum Alpha" />
            </label>
            <label className="field">
              <span>Description</span>
              <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Optional short note" />
            </label>
            <button className="primary-btn" type="submit">Create</button>
          </form>
        </div>

        <div className="card">
          <div className="card-head">
            <h3>Existing projects</h3>
            <button className="ghost-btn" onClick={load}>Refresh</button>
          </div>
          {loading && <p>Loading...</p>}
          {error && <div className="error-box">{error}</div>}
          {!loading && !projects.length && <p>No projects yet. Create one to begin.</p>}
          <div className="list">
            {projects.map((project) => (
              <div key={project.id} className="list-row">
                <div>
                  <div className="title-row">
                    <span className="title">{project.name}</span>
                    <span className="chip">ID {project.id}</span>
                  </div>
                  <p className="muted">{project.description || 'No description'}</p>
                </div>
                <div className="row-actions">
                  <Link to={`/projects/${project.id}/strategies`} className="ghost-btn">Strategies</Link>
                  <button
                    className="danger-btn"
                    onClick={() => handleDelete(project)}
                    disabled={deletingId === project.id}
                  >
                    {deletingId === project.id ? 'Deleting...' : 'Delete'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

import React from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../state/AuthContext.jsx';

export default function MainPage() {
  const { isAuthenticated } = useAuth();

  return (
    <div className="page">
      <section className="hero">
        <div className="pill">Quant strategies, one workspace</div>
        <h1>Ship, test, and monitor trading ideas faster.</h1>
        <p className="lede">
          Connect to the FastAPI backend, manage projects, and iterate on strategies without touching cURL.
        </p>
        <div className="cta-row">
          <Link className="primary-btn" to={isAuthenticated ? '/projects' : '/auth'}>
            {isAuthenticated ? 'Go to projects' : 'Start now'}
          </Link>
          <Link className="ghost-btn" to="/auth">Login / Register</Link>
        </div>
        <div className="grid-preview">
          <div className="card">
            <span className="label">REST Backed</span>
            <p>Talks to /api/auth, /api/projects, and /strategies endpoints with bearer auth.</p>
          </div>
          <div className="card">
            <span className="label">Stateful Auth</span>
            <p>JWT stored locally, guarded routes, and contextual navigation.</p>
          </div>
          <div className="card">
            <span className="label">Project Focused</span>
            <p>Create projects, attach strategies, and drill into details quickly.</p>
          </div>
        </div>
      </section>
    </div>
  );
}

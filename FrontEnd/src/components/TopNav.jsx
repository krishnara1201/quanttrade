import React from 'react';
import { Link } from 'react-router-dom';
import { ChartCandlestick } from 'lucide-react';
import { useAuth } from '../state/AuthContext.jsx';

export default function TopNav() {
  const { isAuthenticated, logout, profile } = useAuth();

  return (
    <header className="nav-shell">
      <Link to="/" className="brand-mark">
        <ChartCandlestick size={20} />
        <span>QuantTrade</span>
      </Link>
      <div className="nav-actions">
        {isAuthenticated ? (
          <>
            <Link to="/projects" className="ghost-btn">Go to projects</Link>
            <span className="chip">{profile?.email}</span>
            <button className="ghost-btn" onClick={logout}>Sign out</button>
          </>
        ) : (
          <Link to="/auth" className="ghost-btn">Sign in</Link>
        )}
      </div>
    </header>
  );
}

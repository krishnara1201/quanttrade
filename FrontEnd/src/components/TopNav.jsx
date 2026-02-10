import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../state/AuthContext.jsx';

export default function TopNav() {
  const { isAuthenticated, logout, profile } = useAuth();
  const location = useLocation();

  const navLinks = [
    { to: '/', label: 'Home' },
    { to: '/projects', label: 'Projects', requireAuth: true },
  ];

  return (
    <header className="nav-shell">
      <div className="brand-mark">
        <div className="dot" />
        <span>QuantTrade</span>
      </div>
      <nav className="nav-links">
        {navLinks.map((item) => {
          if (item.requireAuth && !isAuthenticated) return null;
          const active = location.pathname === item.to;
          return (
            <Link key={item.to} to={item.to} className={active ? 'nav-link active' : 'nav-link'}>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <div className="nav-actions">
        {isAuthenticated ? (
          <>
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

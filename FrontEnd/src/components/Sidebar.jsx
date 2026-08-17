import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ChartCandlestick, Database, Folder, LogOut } from 'lucide-react';
import { useAuth } from '../state/AuthContext.jsx';

const NAV_ITEMS = [
  { to: '/projects', label: 'Projects', icon: Folder },
  { to: '/data', label: 'Data', icon: Database },
];

export default function Sidebar() {
  const { logout, profile } = useAuth();
  const location = useLocation();

  return (
    <aside className="sidebar">
      <Link to="/" className="brand-mark sidebar-brand">
        <ChartCandlestick size={20} />
        <span>QuantTrade</span>
      </Link>
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ to, label, icon: Icon }) => {
          const active = location.pathname === to || location.pathname.startsWith(`${to}/`);
          return (
            <Link key={to} to={to} className={active ? 'sidebar-link active' : 'sidebar-link'}>
              <Icon size={17} />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <span className="sidebar-user">{profile?.email}</span>
        <button className="sidebar-link sidebar-signout" onClick={logout}>
          <LogOut size={17} />
          <span>Sign out</span>
        </button>
      </div>
    </aside>
  );
}

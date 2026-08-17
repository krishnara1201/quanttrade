import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import TopNav from './components/TopNav.jsx';
import Footer from './components/Footer.jsx';
import Sidebar from './components/Sidebar.jsx';
import ProtectedRoute from './components/ProtectedRoute.jsx';
import MainPage from './pages/MainPage.jsx';
import AuthPage from './pages/AuthPage.jsx';
import ProjectsPage from './pages/ProjectsPage.jsx';
import StrategiesPage from './pages/StrategiesPage.jsx';
import BacktestResultsPage from './pages/BacktestResultsPage.jsx';
import DataPage from './pages/DataPage.jsx';

// Marketing/auth pages get the simple top bar; authenticated app pages get
// the persistent sidebar (Dashboard) instead -- different chrome for a
// landing page vs. a workspace, same as most real dashboard products.
function Public({ children }) {
  return (
    <>
      <TopNav />
      <main className="public-main">{children}</main>
      <Footer />
    </>
  );
}

function Dashboard({ children }) {
  return (
    <div className="dashboard-shell">
      <Sidebar />
      <div className="dashboard-main">{children}</div>
    </div>
  );
}

export default function App() {
  return (
    <div className="app-shell">
      <Routes>
        <Route path="/" element={<Public><MainPage /></Public>} />
        <Route path="/auth" element={<Public><AuthPage /></Public>} />
        <Route
          path="/projects"
          element={(
            <ProtectedRoute>
              <Dashboard><ProjectsPage /></Dashboard>
            </ProtectedRoute>
          )}
        />
        <Route
          path="/data"
          element={(
            <ProtectedRoute>
              <Dashboard><DataPage /></Dashboard>
            </ProtectedRoute>
          )}
        />
        <Route
          path="/projects/:projectId/strategies"
          element={(
            <ProtectedRoute>
              <Dashboard><StrategiesPage /></Dashboard>
            </ProtectedRoute>
          )}
        />
        <Route
          path="/strategies/:strategyId/backtest"
          element={(
            <ProtectedRoute>
              <Dashboard><BacktestResultsPage /></Dashboard>
            </ProtectedRoute>
          )}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

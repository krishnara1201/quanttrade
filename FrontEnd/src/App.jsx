import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import TopNav from './components/TopNav.jsx';
import ProtectedRoute from './components/ProtectedRoute.jsx';
import MainPage from './pages/MainPage.jsx';
import AuthPage from './pages/AuthPage.jsx';
import ProjectsPage from './pages/ProjectsPage.jsx';
import StrategiesPage from './pages/StrategiesPage.jsx';
import BacktestResultsPage from './pages/BacktestResultsPage.jsx';

export default function App() {
  return (
    <div className="app-shell">
      <TopNav />
      <Routes>
        <Route path="/" element={<MainPage />} />
        <Route path="/auth" element={<AuthPage />} />
        <Route
          path="/projects"
          element={(
            <ProtectedRoute>
              <ProjectsPage />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/projects/:projectId/strategies"
          element={(
            <ProtectedRoute>
              <StrategiesPage />
            </ProtectedRoute>
          )}
        />
        <Route
          path="/strategies/:strategyId/backtest"
          element={(
            <ProtectedRoute>
              <BacktestResultsPage />
            </ProtectedRoute>
          )}
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  );
}

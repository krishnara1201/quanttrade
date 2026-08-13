import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../state/AuthContext.jsx';

const STEPS = [
  {
    title: 'Create a project',
    body: 'Organize your strategies by project.',
  },
  {
    title: 'Import market data',
    body: 'Upload a CSV of OHLCV bars, or import daily history from Alpha Vantage.',
  },
  {
    title: 'Build a strategy',
    body: 'Use the visual rule builder (indicators + entry/exit conditions), or write custom Python signal logic.',
  },
  {
    title: 'Run a backtest',
    body: 'Pick a ticker, date range, and starting capital.',
  },
  {
    title: 'Review results',
    body: 'Trade log, equity curve, and performance metrics.',
  },
];

const FEATURES = [
  {
    label: 'Visual strategy builder',
    body: 'SMA, EMA, RSI, Bollinger Bands, and MACD indicators, combined into entry/exit rules with no code required.',
  },
  {
    label: 'Custom Python strategies',
    body: 'Write your own signal logic in Python for full control; runs in a restricted, isolated environment.',
  },
  {
    label: 'Market data import',
    body: 'Bring your own CSV, or pull historical daily bars from Alpha Vantage.',
  },
  {
    label: 'Backtest metrics',
    body: 'Total return, win rate, Sharpe ratio, max drawdown, and a full trade-by-trade log.',
  },
  {
    label: 'REST API-backed',
    body: 'Every action goes through a documented FastAPI backend.',
  },
  {
    label: 'Secure by default',
    body: 'JWT-authenticated, project-scoped access to your strategies and results.',
  },
];

const FAQ = [
  {
    q: 'Does this place real trades?',
    a: 'No — QuantTrade is a backtesting platform. It simulates strategies against historical market data; it does not connect to a broker or execute live trades.',
  },
  {
    q: 'Do I need to know how to code?',
    a: 'No — the visual builder covers indicator-based strategies with no code. Custom Python is available if you want more control.',
  },
  {
    q: 'Is my custom strategy code safe to run?',
    a: "Custom code executes in a restricted, isolated environment that only permits pandas/numpy operations — it can't access the network or file system.",
  },
  {
    q: 'Where does market data come from?',
    a: 'Upload your own CSV files, or import daily historical bars from Alpha Vantage directly in the app.',
  },
  {
    q: 'What does a backtest show me?',
    a: 'A full trade log, an equity curve, and summary metrics (return, win rate, Sharpe ratio, max drawdown).',
  },
];

const TABS = [
  { key: 'how', label: 'How it works' },
  { key: 'features', label: 'Features' },
  { key: 'faq', label: 'FAQ' },
];

export default function MainPage() {
  const { isAuthenticated } = useAuth();
  const [activeTab, setActiveTab] = useState('how');

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

      <section className="info-section">
        <div className="info-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`tab${activeTab === tab.key ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === 'how' && (
          <ol className="steps">
            {STEPS.map((step, i) => (
              <li className="step" key={step.title}>
                <span className="step-num">{i + 1}</span>
                <div>
                  <div className="title">{step.title}</div>
                  <p className="muted">{step.body}</p>
                </div>
              </li>
            ))}
          </ol>
        )}

        {activeTab === 'features' && (
          <div className="grid-preview">
            {FEATURES.map((feature) => (
              <div className="card" key={feature.label}>
                <span className="label">{feature.label}</span>
                <p>{feature.body}</p>
              </div>
            ))}
          </div>
        )}

        {activeTab === 'faq' && (
          <div className="list">
            {FAQ.map((item) => (
              <div className="list-row faq-row" key={item.q}>
                <div>
                  <div className="title">{item.q}</div>
                  <p className="muted">{item.a}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

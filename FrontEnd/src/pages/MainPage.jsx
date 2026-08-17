import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { LineChart, Line, ResponsiveContainer } from 'recharts';
import { CodeXml, ShieldCheck, SlidersHorizontal } from 'lucide-react';
import { useAuth } from '../state/AuthContext.jsx';

// Deterministic (not random) illustrative curve for the homepage preview --
// clearly labeled as an example, not a claim about real backtest results.
const EXAMPLE_EQUITY = Array.from({ length: 48 }, (_, i) => ({
  i,
  equity: 10000 + i * 130 + Math.sin(i / 4) * 380 + Math.sin(i / 11) * 220,
  benchmark: 10000 + i * 70 + Math.sin(i / 6) * 150,
}));

const CAPABILITIES = [
  {
    icon: SlidersHorizontal,
    title: '5 built-in indicators',
    body: 'SMA, EMA, RSI, Bollinger Bands, and MACD — combine into entry/exit rules with no code.',
  },
  {
    icon: CodeXml,
    title: '3 backtest modes',
    body: 'Single ticker, weighted-basket portfolio, or walk-forward evaluation for custom Python strategies.',
  },
  {
    icon: ShieldCheck,
    title: 'JWT auth, properly rotated',
    body: 'Short-lived access tokens plus single-use refresh tokens with reuse detection.',
  },
];

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
    body: 'Pick a ticker, date range, and starting capital — or a weighted basket of tickers for a portfolio backtest.',
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
    label: 'Portfolio backtests',
    body: 'Run one strategy across a weighted basket of tickers and see an aggregate equity curve alongside a per-ticker breakdown.',
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
  {
    q: 'Can I backtest a portfolio of tickers, not just one?',
    a: 'Yes — switch a backtest to Portfolio mode, add two or more tickers with custom weights, and get an aggregate equity curve plus a per-ticker breakdown alongside the single-ticker view.',
  },
];

const UNDER_THE_HOOD = [
  {
    title: 'Async by design',
    body: 'Backtests run on a Celery + Redis worker — the API returns immediately instead of blocking on a multi-minute run.',
  },
  {
    title: 'Sandboxed execution',
    body: 'Custom Python strategies run in a restricted subprocess with CPU/memory limits and no network access.',
  },
  {
    title: 'Walk-forward evaluation',
    body: 'Each fold re-fits fresh on an expanding window, stitched into one out-of-sample equity curve.',
  },
  {
    title: 'Benchmark included',
    body: 'Every backtest result ships with a buy-and-hold overlay for comparison.',
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
        <div className="hero-copy">
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
        </div>
        <div className="hero-visual">
          <div className="hero-visual-head">
            <span className="label">Example backtest</span>
            <span className="chip">Illustrative data</span>
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={EXAMPLE_EQUITY} margin={{ top: 4, right: 4, left: 4, bottom: 4 }}>
              <Line type="monotone" dataKey="equity" stroke="#7cf2d4" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line type="monotone" dataKey="benchmark" stroke="#5da2ff" strokeWidth={1.5} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
          <div className="hero-visual-stats">
            <div>
              <span className="label">Return</span>
              <span className="value">+24.6%</span>
            </div>
            <div>
              <span className="label">Sharpe</span>
              <span className="value">1.35</span>
            </div>
            <div>
              <span className="label">Max DD</span>
              <span className="value">-8.2%</span>
            </div>
          </div>
        </div>
      </section>

      <section className="capability-strip">
        {CAPABILITIES.map(({ icon: Icon, title, body }) => (
          <div className="capability" key={title}>
            <Icon size={18} />
            <div>
              <span className="title">{title}</span>
              <p>{body}</p>
            </div>
          </div>
        ))}
      </section>

      <section className="info-layout">
        <div className="info-section">
          <div className="info-tabs">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                type="button"
                className={`underline-tab${activeTab === tab.key ? ' active' : ''}`}
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
            <div className="layout two-cols">
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
        </div>

        <aside className="info-aside">
          <div className="info-aside-head">
            <span className="label">Under the hood</span>
          </div>
          {UNDER_THE_HOOD.map((item) => (
            <div className="info-aside-item" key={item.title}>
              <span className="title">{item.title}</span>
              <p>{item.body}</p>
            </div>
          ))}
        </aside>
      </section>

      <section className="closing-cta">
        <div>
          <h2>Ready to backtest your first strategy?</h2>
          <p className="lede">No credit card, no install — register and import a ticker to get started.</p>
        </div>
        <div className="cta-row">
          <Link className="primary-btn" to={isAuthenticated ? '/projects' : '/auth'}>
            {isAuthenticated ? 'Go to projects' : 'Start now'}
          </Link>
          <Link className="ghost-btn" to="/auth">Login / Register</Link>
        </div>
      </section>
    </div>
  );
}

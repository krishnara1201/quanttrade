import React, { useEffect, useMemo, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import * as strategiesApi from '../api/strategies.js';
import * as backtestApi from '../api/backtest.js';
import * as dataApi from '../api/data.js';
import StrategyBuilder from '../components/StrategyBuilder.jsx';

function toDateInputValue(isoString) {
  return isoString ? isoString.slice(0, 10) : '';
}

function clampDate(value, minIso, maxIso) {
  const min = toDateInputValue(minIso);
  const max = toDateInputValue(maxIso);
  if (!value) return min || value;
  if (min && value < min) return min;
  if (max && value > max) return max;
  return value;
}

export default function StrategiesPage() {
  const { projectId } = useParams();
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showBuilder, setShowBuilder] = useState(false);
  const [selectedStrategyId, setSelectedStrategyId] = useState(null);
  const [backtestForm, setBacktestForm] = useState({
    ticker: '',
    startDate: '2023-01-01',
    endDate: '2024-01-01',
    initialCapital: 10000,
  });
  const [backtestMode, setBacktestMode] = useState('single');
  const [portfolioRows, setPortfolioRows] = useState([
    { ticker: '', weight: 50 },
    { ticker: '', weight: 50 },
  ]);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [tickers, setTickers] = useState([]);
  const [tickersLoading, setTickersLoading] = useState(true);
  const [tickerRange, setTickerRange] = useState(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await strategiesApi.fetchStrategies();
      setStrategies(data || []);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to load strategies');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const loadTickers = async () => {
      setTickersLoading(true);
      try {
        const list = await dataApi.getTickers();
        setTickers(list || []);
        setBacktestForm((prev) => (prev.ticker ? prev : { ...prev, ticker: (list || [])[0] || '' }));
      } catch (err) {
        setError(err?.response?.data?.detail || 'Unable to load tickers');
      } finally {
        setTickersLoading(false);
      }
    };
    loadTickers();
  }, []);

  useEffect(() => {
    if (!backtestForm.ticker) {
      setTickerRange(null);
      return;
    }
    let cancelled = false;
    dataApi
      .getTickerRange(backtestForm.ticker)
      .then((range) => {
        if (cancelled) return;
        setTickerRange(range);
        setBacktestForm((prev) => ({
          ...prev,
          startDate: clampDate(prev.startDate, range.start_date, range.end_date),
          endDate: clampDate(prev.endDate, range.start_date, range.end_date),
        }));
      })
      .catch(() => {
        if (!cancelled) setTickerRange(null);
      });
    return () => {
      cancelled = true;
    };
  }, [backtestForm.ticker]);

  const filtered = useMemo(() => {
    return strategies.filter((s) => String(s.project_id) === String(projectId));
  }, [strategies, projectId]);

  const updatePortfolioRow = (index, field, value) => {
    setPortfolioRows((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const addPortfolioRow = () => {
    setPortfolioRows((prev) => [...prev, { ticker: '', weight: 0 }]);
  };

  const removePortfolioRow = (index) => {
    setPortfolioRows((prev) => prev.filter((_, i) => i !== index));
  };

  const validPortfolioRows = portfolioRows.filter((r) => r.ticker && Number(r.weight) > 0);

  const handleSaveStrategy = async (strategyConfig) => {
    setError('');
    try {
      const parameters = strategyConfig.mode === 'custom_code'
        ? { name: strategyConfig.name, mode: 'custom_code' }
        : { name: strategyConfig.name, mode: 'rules', parameters: strategyConfig.parameters, rules: strategyConfig.rules };

      const created = await strategiesApi.createStrategy({
        name: strategyConfig.name,
        status: 'draft',
        project_id: Number(projectId),
        parameters: JSON.stringify(parameters),
        ...(strategyConfig.mode === 'custom_code' ? { code: strategyConfig.code } : {}),
      });
      setStrategies((prev) => [...prev, created]);
      setShowBuilder(false);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Could not create strategy');
    }
  };

  const handleRunBacktest = async (e) => {
    e.preventDefault();
    if (!selectedStrategyId) {
      setError('Please select a strategy first');
      return;
    }
    setBacktestLoading(true);
    setError('');
    try {
      if (backtestMode === 'portfolio') {
        await backtestApi.runPortfolioBacktest(
          selectedStrategyId,
          validPortfolioRows.map((r) => ({ ticker: r.ticker, weight: Number(r.weight) })),
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital
        );
      } else {
        await backtestApi.runBacktest(
          selectedStrategyId,
          backtestForm.ticker,
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital
        );
      }
      // Redirect to results page
      window.location.href = `/strategies/${selectedStrategyId}/backtest`;
    } catch (err) {
      setError(err?.response?.data?.detail || 'Backtest failed');
    } finally {
      setBacktestLoading(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <p className="pill">Project {projectId}</p>
        <h1>Strategies</h1>
        <p className="lede">Create, configure, and backtest trading strategies.</p>
      </div>

      {showBuilder ? (
        <div className="card">
          <StrategyBuilder
            onSave={handleSaveStrategy}
            onCancel={() => setShowBuilder(false)}
          />
          {error && <div className="error-box" style={{ marginTop: '12px' }}>{error}</div>}
        </div>
      ) : (
        <>
          <div className="layout two-cols">
            <div className="card">
              <h3>Create a new strategy</h3>
              <p className="muted">Build strategies visually, or write custom Python signal logic.</p>
              <button className="primary-btn" onClick={() => setShowBuilder(true)}>
                Open Strategy Builder
              </button>
              {error && <div className="error-box" style={{ marginTop: '12px' }}>{error}</div>}
            </div>

            <div className="card">
              <div className="card-head">
                <h3>Project strategies</h3>
                <button className="ghost-btn" onClick={load}>Refresh</button>
              </div>
              {loading && <p>Loading...</p>}
              {!loading && !filtered.length && <p>No strategies for this project yet.</p>}
              <div className="list">
                {filtered.map((s) => {
                  let config;
                  try {
                    config = typeof s.parameters === 'string' ? JSON.parse(s.parameters) : s.parameters;
                  } catch {
                    config = { name: s.name };
                  }
                  return (
                    <div key={s.id} className="list-row">
                      <div style={{ flex: 1 }}>
                        <div className="title-row">
                          <span className="title">{s.name}</span>
                          <span className="chip">{s.status || 'draft'}</span>
                        </div>
                        <p className="muted">
                          {config.mode === 'custom_code'
                            ? 'Custom Python strategy'
                            : (
                              <>
                                {config.rules?.entry && `Entry: ${config.rules.entry}`}
                                {config.rules?.exit && ` • Exit: ${config.rules.exit}`}
                              </>
                            )}
                        </p>
                      </div>
                      <Link to={`/strategies/${s.id}/backtest`} className="ghost-btn">Results</Link>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Backtest Panel */}
          <div className="card" style={{ marginTop: '20px' }}>
            <h3>Run Backtest</h3>
            <form className="stack" onSubmit={handleRunBacktest}>
              <div className="title-row" style={{ marginBottom: '8px' }}>
                <button
                  type="button"
                  className={backtestMode === 'single' ? 'primary-btn' : 'ghost-btn'}
                  onClick={() => setBacktestMode('single')}
                >
                  Single ticker
                </button>
                <button
                  type="button"
                  className={backtestMode === 'portfolio' ? 'primary-btn' : 'ghost-btn'}
                  onClick={() => setBacktestMode('portfolio')}
                >
                  Portfolio
                </button>
              </div>

              <div className="layout two-cols">
                <label className="field">
                  <span>Select Strategy</span>
                  <select
                    value={selectedStrategyId || ''}
                    onChange={(e) => setSelectedStrategyId(Number(e.target.value) || null)}
                  >
                    <option value="">Choose a strategy...</option>
                    {filtered.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>

                {backtestMode === 'single' && (
                  <label className="field">
                    <span>Ticker</span>
                    <select
                      value={backtestForm.ticker}
                      onChange={(e) => setBacktestForm({ ...backtestForm, ticker: e.target.value })}
                      disabled={tickersLoading || !tickers.length}
                    >
                      {!tickers.length && (
                        <option value="">
                          {tickersLoading ? 'Loading tickers...' : 'No market data uploaded yet'}
                        </option>
                      )}
                      {tickers.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                    {tickerRange && (
                      <span className="muted" style={{ fontSize: '0.8em' }}>
                        Data available {toDateInputValue(tickerRange.start_date)} to {toDateInputValue(tickerRange.end_date)}
                        {' '}({tickerRange.count} bars)
                      </span>
                    )}
                  </label>
                )}

                <label className="field">
                  <span>Start Date</span>
                  <input
                    type="date"
                    value={backtestForm.startDate}
                    min={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.start_date) : undefined}
                    max={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.end_date) : undefined}
                    onChange={(e) => setBacktestForm({ ...backtestForm, startDate: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>End Date</span>
                  <input
                    type="date"
                    value={backtestForm.endDate}
                    min={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.start_date) : undefined}
                    max={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.end_date) : undefined}
                    onChange={(e) => setBacktestForm({ ...backtestForm, endDate: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Initial Capital</span>
                  <input
                    type="number"
                    value={backtestForm.initialCapital}
                    onChange={(e) => setBacktestForm({ ...backtestForm, initialCapital: Number(e.target.value) })}
                  />
                </label>
              </div>

              {backtestMode === 'portfolio' && (
                <div className="stack" style={{ marginTop: '8px' }}>
                  <span className="muted" style={{ fontSize: '0.85em' }}>
                    Weights are relative — they don't need to sum to 100 (e.g. 2 and 1 splits 66/33).
                  </span>
                  {portfolioRows.map((row, index) => (
                    <div key={index} className="layout two-cols" style={{ alignItems: 'end' }}>
                      <label className="field">
                        <span>Ticker</span>
                        <select
                          value={row.ticker}
                          onChange={(e) => updatePortfolioRow(index, 'ticker', e.target.value)}
                          disabled={tickersLoading || !tickers.length}
                        >
                          <option value="">Choose a ticker...</option>
                          {tickers.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>Weight</span>
                        <input
                          type="number"
                          min="0"
                          value={row.weight}
                          onChange={(e) => updatePortfolioRow(index, 'weight', e.target.value)}
                        />
                      </label>
                      <button
                        type="button"
                        className="ghost-btn"
                        onClick={() => removePortfolioRow(index)}
                        disabled={portfolioRows.length <= 2}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <button type="button" className="ghost-btn" onClick={addPortfolioRow}>
                    Add ticker
                  </button>
                </div>
              )}

              <button
                className="primary-btn"
                type="submit"
                disabled={
                  backtestLoading ||
                  !selectedStrategyId ||
                  (backtestMode === 'single' ? !backtestForm.ticker : validPortfolioRows.length < 2)
                }
              >
                {backtestLoading ? 'Running...' : 'Run Backtest'}
              </button>
            </form>
          </div>
        </>
      )}
    </div>
  );
}

import React, { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import BacktestChart from '../components/BacktestChart.jsx';
import * as backtestApi from '../api/backtest.js';

export default function BacktestResultsPage() {
  const { strategyId } = useParams();
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedResult, setSelectedResult] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedTicker, setExpandedTicker] = useState(null);

  const selectedResultRef = useRef(null);
  useEffect(() => {
    selectedResultRef.current = selectedResult;
  }, [selectedResult]);

  const loadResults = async ({ preserveSelection = false } = {}) => {
    if (!preserveSelection) setLoading(true);
    setError('');
    try {
      const [singleResults, portfolioResults, walkForwardResults] = await Promise.all([
        backtestApi.getBacktestResults(strategyId),
        backtestApi.getPortfolioBacktestResults(strategyId),
        backtestApi.getWalkForwardBacktestResults(strategyId),
      ]);
      const merged = [
        ...(singleResults || []).map((r) => ({ ...r, _type: 'single' })),
        ...(portfolioResults || []).map((r) => ({ ...r, _type: 'portfolio' })),
        ...(walkForwardResults || []).map((r) => ({ ...r, _type: 'walk_forward' })),
      ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      setResults(merged);
      if (!preserveSelection && merged.length > 0) {
        loadDetail(merged[0]);
      } else if (preserveSelection && selectedResultRef.current) {
        const prev = selectedResultRef.current;
        const updated = merged.find((r) => r.id === prev.id && r._type === prev._type);
        const finished = updated && updated.status !== prev.status && (updated.status === 'success' || updated.status === 'failed');
        const progressed = updated && updated._type === 'walk_forward' && updated.folds_completed !== prev.folds_completed;
        if (finished || progressed) {
          loadDetail(updated);
        }
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest results');
    } finally {
      if (!preserveSelection) setLoading(false);
    }
  };

  const loadDetail = async (result) => {
    setDetailLoading(true);
    setExpandedTicker(null);
    try {
      let data;
      if (result._type === 'portfolio') {
        data = await backtestApi.getPortfolioBacktestDetail(result.id);
      } else if (result._type === 'walk_forward') {
        data = await backtestApi.getWalkForwardBacktestDetail(result.id);
      } else {
        data = await backtestApi.getBacktestDetail(result.id);
      }
      setSelectedResult({ ...data, _type: result._type });
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest details');
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    loadResults();
  }, [strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const active = results.some((r) => r.status === 'pending' || r.status === 'running');
    if (!active) return undefined;
    const timer = setInterval(() => {
      loadResults({ preserveSelection: true });
    }, 2500);
    return () => clearInterval(timer);
  }, [results, strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-head">
        <p className="pill">Strategy {strategyId}</p>
        <h1>Backtest Results</h1>
      </div>

      <div className="layout two-cols">
        {/* Results List */}
        <div className="card">
          <div className="card-head">
            <h3>Previous backtests</h3>
            <button className="ghost-btn" onClick={loadResults}>Refresh</button>
          </div>
          {loading && <p>Loading...</p>}
          {error && <div className="error-box">{error}</div>}
          {!loading && results.length === 0 && <p className="muted">No backtest results yet.</p>}
          <div className="list">
            {results.map((result) => (
              <div
                key={`${result._type}-${result.id}`}
                className={`list-row ${selectedResult?.id === result.id && selectedResult?._type === result._type ? 'active' : ''}`}
                onClick={() => loadDetail(result)}
                style={{ cursor: 'pointer' }}
              >
                <div>
                  <div className="title-row">
                    <span className="title">Backtest #{result.id}</span>
                    {result._type === 'portfolio' && (
                      <span className="chip">Portfolio · {result.allocations?.length || 0} tickers</span>
                    )}
                    {result._type === 'walk_forward' && (
                      <span className="chip">
                        Walk-forward · {result.status === 'running'
                          ? `Fold ${result.folds_completed || 0}/${result.total_folds || '?'}`
                          : `${result.total_folds || 0} folds`}
                      </span>
                    )}
                    {result._type === 'single' && (
                      <span className="chip">{result.num_trades} trades</span>
                    )}
                    {result.status && result.status !== 'success' && (
                      <span className="chip">{result.status}</span>
                    )}
                  </div>
                  <p className="muted">{result.created_at}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Details */}
        <div className="card">
          {detailLoading && <p>Loading details...</p>}
          {selectedResult && (
            <>
              <h3>Performance Metrics</h3>
              {selectedResult.status && selectedResult.status !== 'success' && (
                <div
                  className={selectedResult.status === 'failed' ? 'error-box' : 'muted'}
                  style={{ marginBottom: '12px' }}
                >
                  {selectedResult.status === 'failed'
                    ? `Backtest failed: ${selectedResult.error_message || 'Unknown error'}`
                    : selectedResult._type === 'walk_forward'
                      ? `Running fold ${selectedResult.folds_completed || 0} of ${selectedResult.total_folds || '?'}…`
                      : `Backtest ${selectedResult.status}…`}
                </div>
              )}
              <div className="metrics-grid">
                <div className="metric">
                  <span className="label">Total Return</span>
                  <span className="value">${selectedResult.metrics?.total_return?.toFixed(2) || 0}</span>
                </div>
                <div className="metric">
                  <span className="label">Return %</span>
                  <span className="value">{selectedResult.metrics?.return_pct?.toFixed(2) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Win Rate</span>
                  <span className="value">{selectedResult.metrics?.win_rate?.toFixed(1) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Trades</span>
                  <span className="value">{selectedResult.metrics?.num_trades || 0}</span>
                </div>
                <div className="metric">
                  <span className="label">Max Drawdown</span>
                  <span className="value">{selectedResult.metrics?.max_drawdown_pct?.toFixed(2) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Sharpe Ratio</span>
                  <span className="value">{selectedResult.metrics?.sharpe_ratio?.toFixed(2) || 0}</span>
                </div>
              </div>

              {selectedResult._type === 'portfolio' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Per-ticker summary</h3>
                  <div className="list">
                    {(selectedResult.allocations || []).map((alloc) => {
                      const tickerResult = selectedResult.per_ticker?.[alloc.ticker];
                      return (
                        <div key={alloc.ticker} className="list-row">
                          <div className="title-row">
                            <span className="title">{alloc.ticker}</span>
                            <span className="chip">{(alloc.weight * 100).toFixed(1)}% weight</span>
                          </div>
                          <p className="muted">
                            Return {tickerResult?.metrics?.return_pct?.toFixed(2) || 0}%
                            {' · '}
                            {tickerResult?.metrics?.num_trades || 0} trades
                            {' · '}
                            Win rate {tickerResult?.metrics?.win_rate?.toFixed(1) || 0}%
                          </p>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {selectedResult._type === 'walk_forward' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Per-fold breakdown</h3>
                  <p className="muted">
                    Trades are settled at each fold boundary — any position still open at the end of a
                    fold is closed into the next fold's starting capital with no commission/slippage and
                    no recorded exit trade, so trade counts and returns here aren't directly comparable
                    to a single continuous backtest over the same period.
                  </p>
                  <div className="trades-list">
                    {(selectedResult.folds || []).map((fold) => (
                      <div key={fold.fold_index} className="trade-item">
                        <span className="badge">Fold {fold.fold_index + 1}</span>
                        <span className="muted">
                          Train {fold.train_start.slice(0, 10)} → {fold.train_end.slice(0, 10)}
                        </span>
                        <span className="muted">
                          Test {fold.test_start.slice(0, 10)} → {fold.test_end.slice(0, 10)}
                        </span>
                        <span className={fold.return_pct > 0 ? 'profit' : 'loss'}>
                          {fold.return_pct > 0 ? '+' : ''}{fold.return_pct.toFixed(2)}%
                        </span>
                        <span className="muted">{fold.num_trades} trades</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {selectedResult._type === 'single' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Trades</h3>
                  <div className="trades-list">
                    {selectedResult.trades && selectedResult.trades.map((trade, idx) => (
                      <div key={idx} className="trade-item">
                        <span className={`badge ${trade.type === 'entry' ? 'entry' : 'exit'}`}>
                          {trade.type.toUpperCase()}
                        </span>
                        <span className={`badge direction-${trade.direction === 'short' ? 'short' : 'long'}`}>
                          {trade.direction === 'short' ? 'SHORT' : 'LONG'}
                        </span>
                        <span>${trade.price.toFixed(2)}</span>
                        <span className="muted">{trade.date}</span>
                        {trade.exit_reason && trade.exit_reason !== 'signal' && (
                          <span className="muted">
                            {trade.exit_reason === 'stop_loss' ? 'Stopped out' : 'Took profit'}
                          </span>
                        )}
                        {trade.pnl && <span className={trade.pnl > 0 ? 'profit' : 'loss'}>
                          {trade.pnl > 0 ? '+' : ''}{trade.pnl.toFixed(2)}
                        </span>}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* Aggregate chart */}
      {selectedResult && selectedResult._type === 'single' && (
        <div className="card">
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals}
            trades={selectedResult.trades}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'portfolio' && (
        <div className="card">
          <h3>Per-ticker breakdown</h3>
          <div className="list">
            {(selectedResult.allocations || []).map((alloc) => {
              const tickerResult = selectedResult.per_ticker?.[alloc.ticker];
              return (
                <div key={alloc.ticker}>
                  <div
                    className="list-row"
                    onClick={() => setExpandedTicker(expandedTicker === alloc.ticker ? null : alloc.ticker)}
                    style={{ cursor: 'pointer' }}
                  >
                    <div className="expand-row">
                      {expandedTicker === alloc.ticker ? (
                        <ChevronDown size={15} />
                      ) : (
                        <ChevronRight size={15} />
                      )}
                      <div>
                        <div className="title-row">
                          <span className="title">{alloc.ticker}</span>
                          <span className="chip">{(alloc.weight * 100).toFixed(1)}% weight</span>
                        </div>
                        <p className="muted">
                          Return {tickerResult?.metrics?.return_pct?.toFixed(2) || 0}%
                          {' · '}
                          {tickerResult?.metrics?.num_trades || 0} trades
                          {' · '}
                          Win rate {tickerResult?.metrics?.win_rate?.toFixed(1) || 0}%
                        </p>
                      </div>
                    </div>
                  </div>
                  {expandedTicker === alloc.ticker && tickerResult && (
                    <BacktestChart
                      data={tickerResult.signals}
                      trades={tickerResult.trades}
                      equityCurve={tickerResult.equity_curve}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {selectedResult && selectedResult._type === 'portfolio' && (
        <div className="card">
          <h3>Portfolio Equity</h3>
          <BacktestChart
            data={[]}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
            equityName="Portfolio Equity"
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'walk_forward' && (
        <div className="card">
          <h3>Walk-Forward OOS Equity</h3>
          <BacktestChart
            data={[]}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
            equityName="Walk-Forward OOS Equity"
          />
        </div>
      )}
    </div>
  );
}

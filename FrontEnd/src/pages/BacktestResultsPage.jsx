import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
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

  const loadResults = async () => {
    setLoading(true);
    setError('');
    try {
      const [singleResults, portfolioResults] = await Promise.all([
        backtestApi.getBacktestResults(strategyId),
        backtestApi.getPortfolioBacktestResults(strategyId),
      ]);
      const merged = [
        ...(singleResults || []).map((r) => ({ ...r, _type: 'single' })),
        ...(portfolioResults || []).map((r) => ({ ...r, _type: 'portfolio' })),
      ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      setResults(merged);
      if (merged.length > 0) {
        loadDetail(merged[0]);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest results');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (result) => {
    setDetailLoading(true);
    setExpandedTicker(null);
    try {
      const data = result._type === 'portfolio'
        ? await backtestApi.getPortfolioBacktestDetail(result.id)
        : await backtestApi.getBacktestDetail(result.id);
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
                    {result._type === 'portfolio' ? (
                      <span className="chip">Portfolio · {result.allocations?.length || 0} tickers</span>
                    ) : (
                      <span className="chip">{result.num_trades} trades</span>
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

              {selectedResult._type === 'portfolio' ? (
                <>
                  <h3 style={{ marginTop: '20px' }}>Per-ticker breakdown</h3>
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
                </>
              ) : (
                <>
                  <h3 style={{ marginTop: '20px' }}>Trades</h3>
                  <div className="trades-list">
                    {selectedResult.trades && selectedResult.trades.map((trade, idx) => (
                      <div key={idx} className="trade-item">
                        <span className={`badge ${trade.type === 'entry' ? 'entry' : 'exit'}`}>
                          {trade.type.toUpperCase()}
                        </span>
                        <span>${trade.price.toFixed(2)}</span>
                        <span className="muted">{trade.date}</span>
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

      {/* Aggregate chart (single-ticker: price+signals+equity; portfolio: equity only, per-ticker charts are inline above) */}
      {selectedResult && selectedResult._type === 'single' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals}
            trades={selectedResult.trades}
            equityCurve={selectedResult.equity_curve}
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'portfolio' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Portfolio Equity</h3>
          <BacktestChart
            data={(selectedResult.equity_curve || []).map((p) => ({ date: p.date, close: p.equity, signal: 0 }))}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
          />
        </div>
      )}
    </div>
  );
}

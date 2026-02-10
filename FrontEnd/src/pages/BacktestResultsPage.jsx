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

  const loadResults = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await backtestApi.getBacktestResults(strategyId);
      setResults(data || []);
      if (data.length > 0) {
        loadDetail(data[0].id);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest results');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (backtestId) => {
    setDetailLoading(true);
    try {
      const data = await backtestApi.getBacktestDetail(backtestId);
      setSelectedResult(data);
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
                key={result.id}
                className={`list-row ${selectedResult?.id === result.id ? 'active' : ''}`}
                onClick={() => loadDetail(result.id)}
                style={{ cursor: 'pointer' }}
              >
                <div>
                  <div className="title-row">
                    <span className="title">Backtest #{result.id}</span>
                    <span className="chip">{result.num_trades} trades</span>
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
              </div>

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
        </div>
      </div>

      {/* Chart */}
      {selectedResult && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals?.map((d, idx) => ({
              ...d,
              index: idx,
            }))}
            trades={selectedResult.trades?.map((t, idx) => ({
              ...t,
              index: idx,
            })) || []}
          />
        </div>
      )}
    </div>
  );
}

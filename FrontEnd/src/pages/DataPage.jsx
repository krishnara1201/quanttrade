import React, { useEffect, useState } from 'react';
import * as dataApi from '../api/data.js';

const API_KEY_STORAGE_KEY = 'quanttrade_alpha_vantage_api_key';

export default function DataPage() {
  const [tickers, setTickers] = useState([]);
  const [tickerDetails, setTickerDetails] = useState({});
  const [tickersLoading, setTickersLoading] = useState(true);

  const [webForm, setWebForm] = useState({ ticker: '', startDate: '', endDate: '' });
  const [apiKey, setApiKey] = useState(() => localStorage.getItem(API_KEY_STORAGE_KEY) || '');
  const [webLoading, setWebLoading] = useState(false);
  const [webError, setWebError] = useState('');
  const [webResult, setWebResult] = useState(null);

  const [csvForm, setCsvForm] = useState({ ticker: '' });
  const [csvFile, setCsvFile] = useState(null);
  const [csvLoading, setCsvLoading] = useState(false);
  const [csvError, setCsvError] = useState('');
  const [csvResult, setCsvResult] = useState(null);

  const loadTickers = async () => {
    setTickersLoading(true);
    try {
      const list = await dataApi.getTickers();
      setTickers(list || []);
      const entries = await Promise.all(
        (list || []).map(async (t) => {
          try {
            return [t, await dataApi.getTickerRange(t)];
          } catch {
            return [t, null];
          }
        })
      );
      setTickerDetails(Object.fromEntries(entries));
    } finally {
      setTickersLoading(false);
    }
  };

  useEffect(() => {
    loadTickers();
  }, []);

  const handleApiKeyChange = (value) => {
    setApiKey(value);
    if (value) {
      localStorage.setItem(API_KEY_STORAGE_KEY, value);
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY);
    }
  };

  const handleWebImport = async (e) => {
    e.preventDefault();
    setWebLoading(true);
    setWebError('');
    setWebResult(null);
    try {
      const result = await dataApi.importMarketDataFromWeb(
        webForm.ticker.trim().toUpperCase(),
        webForm.startDate,
        webForm.endDate,
        apiKey.trim()
      );
      setWebResult(result);
      await loadTickers();
    } catch (err) {
      setWebError(err?.response?.data?.detail || 'Import failed');
    } finally {
      setWebLoading(false);
    }
  };

  const handleCsvUpload = async (e) => {
    e.preventDefault();
    if (!csvFile) {
      setCsvError('Choose a CSV file first');
      return;
    }
    setCsvLoading(true);
    setCsvError('');
    setCsvResult(null);
    try {
      const result = await dataApi.uploadMarketDataCsv(csvForm.ticker.trim().toUpperCase(), csvFile);
      setCsvResult(result);
      await loadTickers();
    } catch (err) {
      setCsvError(err?.response?.data?.detail || 'Upload failed');
    } finally {
      setCsvLoading(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <p className="pill">Market data</p>
        <h1>Import historical data</h1>
        <p className="lede">
          Load OHLCV history by ticker so it's available to run backtests against.
        </p>
      </div>

      <div className="layout two-cols">
        <div className="card">
          <h3>Import from the web</h3>
          <p className="muted">
            Fetches daily history from Alpha Vantage by ticker symbol. Needs a free API key
            (no card, under a minute to claim at{' '}
            <a href="https://www.alphavantage.co/support/#api-key" target="_blank" rel="noreferrer">
              alphavantage.co
            </a>
            ) — paste it below and it's remembered on this device. Without a key, only
            Alpha Vantage's demo symbol (IBM) will work. Leave the dates blank to pull full
            available history.
          </p>
          <form className="stack" onSubmit={handleWebImport}>
            <label className="field">
              <span>Ticker</span>
              <input
                value={webForm.ticker}
                onChange={(e) => setWebForm({ ...webForm, ticker: e.target.value })}
                placeholder="AAPL"
                required
              />
            </label>
            <label className="field">
              <span>Alpha Vantage API Key (optional — falls back to server default / demo)</span>
              <input
                value={apiKey}
                onChange={(e) => handleApiKeyChange(e.target.value)}
                placeholder="demo"
              />
            </label>
            <label className="field">
              <span>Start Date (optional)</span>
              <input
                type="date"
                value={webForm.startDate}
                onChange={(e) => setWebForm({ ...webForm, startDate: e.target.value })}
              />
            </label>
            <label className="field">
              <span>End Date (optional)</span>
              <input
                type="date"
                value={webForm.endDate}
                onChange={(e) => setWebForm({ ...webForm, endDate: e.target.value })}
              />
            </label>
            <button className="primary-btn" type="submit" disabled={webLoading || !webForm.ticker.trim()}>
              {webLoading ? 'Importing...' : 'Fetch & Import'}
            </button>
          </form>
          {webError && <div className="error-box" style={{ marginTop: '12px' }}>{webError}</div>}
          {webResult && (
            <p className="muted" style={{ marginTop: '12px' }}>
              {webResult.ticker}: {webResult.inserted} bar(s) imported, {webResult.skipped} already on file.
            </p>
          )}
        </div>

        <div className="card">
          <h3>Upload a CSV</h3>
          <p className="muted">
            For data you already have (broker export, Yahoo Finance download, etc). Expects
            columns Date, Open, High, Low, Close, Volume (Adj Close optional).
          </p>
          <form className="stack" onSubmit={handleCsvUpload}>
            <label className="field">
              <span>Ticker</span>
              <input
                value={csvForm.ticker}
                onChange={(e) => setCsvForm({ ...csvForm, ticker: e.target.value })}
                placeholder="AAPL"
                required
              />
            </label>
            <label className="field">
              <span>CSV File</span>
              <input
                type="file"
                accept=".csv"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                required
              />
            </label>
            <button className="primary-btn" type="submit" disabled={csvLoading || !csvForm.ticker.trim() || !csvFile}>
              {csvLoading ? 'Uploading...' : 'Upload'}
            </button>
          </form>
          {csvError && <div className="error-box" style={{ marginTop: '12px' }}>{csvError}</div>}
          {csvResult && (
            <p className="muted" style={{ marginTop: '12px' }}>
              {csvResult.ticker}: {csvResult.inserted} bar(s) imported, {csvResult.skipped} already on file.
            </p>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: '20px' }}>
        <div className="card-head">
          <h3>Data on file</h3>
          <button className="ghost-btn" onClick={loadTickers}>Refresh</button>
        </div>
        {tickersLoading && <p>Loading...</p>}
        {!tickersLoading && !tickers.length && <p>No market data imported yet.</p>}
        <div className="list">
          {tickers.map((t) => {
            const details = tickerDetails[t];
            return (
              <div key={t} className="list-row">
                <div>
                  <div className="title-row">
                    <span className="title">{t}</span>
                    {details && <span className="chip">{details.count} bars</span>}
                  </div>
                  {details && (
                    <p className="muted">
                      {details.start_date?.slice(0, 10)} to {details.end_date?.slice(0, 10)}
                    </p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

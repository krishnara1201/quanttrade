import React, { useEffect, useState } from 'react';
import * as dataApi from '../api/data.js';
import { pollUntil } from '../api/pollUntil.js';
import MarketDataChart from '../components/MarketDataChart.jsx';

const API_KEY_STORAGE_KEY = 'quanttrade_alpha_vantage_api_key';

export default function DataPage() {
  const [tickers, setTickers] = useState([]);
  const [tickerDetails, setTickerDetails] = useState({});
  const [tickersLoading, setTickersLoading] = useState(true);
  const [deletingTicker, setDeletingTicker] = useState(null);
  const [deleteNotices, setDeleteNotices] = useState({});

  const [chartTicker, setChartTicker] = useState('');
  const [chartRange, setChartRange] = useState({ startDate: '', endDate: '' });
  const [appliedRange, setAppliedRange] = useState({ startDate: '', endDate: '' });
  const [chartData, setChartData] = useState([]);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState('');

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

  useEffect(() => {
    if (!chartTicker && tickers.length) {
      setChartTicker(tickers[0]);
    }
  }, [tickers, chartTicker]);

  useEffect(() => {
    if (!chartTicker) return;
    const details = tickerDetails[chartTicker];
    const startDate = details?.start_date?.slice(0, 10) || '';
    const endDate = details?.end_date?.slice(0, 10) || '';
    setChartRange({ startDate, endDate });
    setAppliedRange({ startDate, endDate });
  }, [chartTicker, tickerDetails]);

  useEffect(() => {
    if (!chartTicker || !appliedRange.startDate || !appliedRange.endDate) return;
    let cancelled = false;
    setChartLoading(true);
    setChartError('');
    dataApi.getHistoricalData(chartTicker, appliedRange.startDate, appliedRange.endDate)
      .then((rows) => {
        if (!cancelled) setChartData(rows || []);
      })
      .catch((err) => {
        if (!cancelled) setChartError(err?.response?.data?.detail || err.message || 'Failed to load chart data');
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => { cancelled = true; };
  }, [chartTicker, appliedRange.startDate, appliedRange.endDate]);

  const handleUpdateCharts = () => {
    setAppliedRange({ startDate: chartRange.startDate, endDate: chartRange.endDate });
  };

  const chartRangeDirty = chartRange.startDate !== appliedRange.startDate || chartRange.endDate !== appliedRange.endDate;

  const handleDeleteTicker = async (ticker) => {
    const details = tickerDetails[ticker];
    const deletable = details?.deletable_count;
    const total = details?.count;
    const confirmMessage = deletable != null && deletable < total
      ? `Only ${deletable} of ${total} stored bar(s) for ${ticker} were imported by you — the rest belong to another user and will be left in place. Delete your ${deletable} bar(s)? This cannot be undone.`
      : `Delete all stored data for ${ticker}? This cannot be undone.`;
    if (!window.confirm(confirmMessage)) {
      return;
    }
    setDeletingTicker(ticker);
    setDeleteNotices((prev) => ({ ...prev, [ticker]: null }));
    try {
      const result = await dataApi.deleteTickerData(ticker);
      if (result.deleted === total) {
        if (chartTicker === ticker) {
          setChartTicker('');
          setChartData([]);
        }
      } else {
        const remaining = total - result.deleted;
        setDeleteNotices((prev) => ({
          ...prev,
          [ticker]: result.deleted === 0
            ? `None of this ticker's data was imported by you — nothing was deleted.`
            : `Deleted ${result.deleted} of ${total} bar(s). The remaining ${remaining} were imported by another user and can't be removed.`,
        }));
      }
      await loadTickers();
    } catch (err) {
      setDeleteNotices((prev) => ({
        ...prev,
        [ticker]: err?.response?.data?.detail || err.message || 'Delete failed',
      }));
    } finally {
      setDeletingTicker(null);
    }
  };

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
      const { job_id } = await dataApi.importMarketDataFromWeb(
        webForm.ticker.trim().toUpperCase(),
        webForm.startDate,
        webForm.endDate,
        apiKey.trim()
      );
      const job = await pollUntil(
        () => dataApi.getImportJob(job_id),
        (j) => j.status === 'success' || j.status === 'failed'
      );
      if (job.status === 'failed') {
        setWebError(job.error_message || 'Import failed');
      } else {
        setWebResult(job.result);
        await loadTickers();
      }
    } catch (err) {
      setWebError(err?.response?.data?.detail || err.message || 'Import failed');
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
      const { job_id } = await dataApi.uploadMarketDataCsv(csvForm.ticker.trim().toUpperCase(), csvFile);
      const job = await pollUntil(
        () => dataApi.getImportJob(job_id),
        (j) => j.status === 'success' || j.status === 'failed'
      );
      if (job.status === 'failed') {
        setCsvError(job.error_message || 'Upload failed');
      } else {
        setCsvResult(job.result);
        await loadTickers();
      }
    } catch (err) {
      setCsvError(err?.response?.data?.detail || err.message || 'Upload failed');
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
          <div className="card-head">
            <h3>Import from the web</h3>
            <span className="chip">Daily bars only</span>
          </div>
          <p className="muted">
            Fetches daily history from Alpha Vantage by ticker symbol. Needs a free API key
            (no card, under a minute to claim at{' '}
            <a href="https://www.alphavantage.co/support/#api-key" target="_blank" rel="noreferrer">
              alphavantage.co
            </a>
            ) — paste it below and it's remembered on this device. Without a key, only
            Alpha Vantage's demo symbol (IBM) will work. Pulls the last ~100 daily bars
            (Alpha Vantage's free tier no longer allows fetching full history); date filters
            below just trim that window.
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
          <div className="card-head">
            <h3>Upload a CSV or TXT</h3>
            <span className="chip">Daily bars only</span>
          </div>
          <p className="muted">
            For data you already have. Two shapes are supported: a CSV with a header row
            (Date, Open, High, Low, Close, Volume, Adj Close optional — e.g. a broker/Yahoo
            Finance export; ticker is required below), or a headerless Stooq-style per-symbol
            .txt export (<code>TICKER.US,D,YYYYMMDD,HHMMSS,Open,High,Low,Close,Volume,OpenInt</code>)
            — the ticker is read from the file itself, so it can be left blank, and a file
            covering more than one symbol is split and imported per-ticker automatically.
            One row per trading day — intraday or sub-daily bars aren't supported.
          </p>
          <form className="stack" onSubmit={handleCsvUpload}>
            <label className="field">
              <span>Ticker (required for plain CSVs; inferred automatically from .txt files)</span>
              <input
                value={csvForm.ticker}
                onChange={(e) => setCsvForm({ ...csvForm, ticker: e.target.value })}
                placeholder="AAPL"
              />
            </label>
            <label className="field">
              <span>File</span>
              <input
                type="file"
                accept=".csv,.txt"
                onChange={(e) => setCsvFile(e.target.files?.[0] || null)}
                required
              />
            </label>
            <button className="primary-btn" type="submit" disabled={csvLoading || !csvFile}>
              {csvLoading ? 'Uploading...' : 'Upload'}
            </button>
          </form>
          {csvError && <div className="error-box" style={{ marginTop: '12px' }}>{csvError}</div>}
          {csvResult && (
            <div className="muted" style={{ marginTop: '12px' }}>
              {(Array.isArray(csvResult) ? csvResult : [csvResult]).map((r) => (
                <p key={r.ticker}>{r.ticker}: {r.inserted} bar(s) imported, {r.skipped} already on file.</p>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Data on file</h3>
          <button className="ghost-btn" onClick={loadTickers}>Refresh</button>
        </div>
        {tickersLoading && <p>Loading...</p>}
        {!tickersLoading && !tickers.length && <p>No market data imported yet.</p>}
        <div className="list">
          {tickers.map((t) => {
            const details = tickerDetails[t];
            const deletable = details?.deletable_count;
            const total = details?.count;
            const noneDeletable = details && deletable === 0;
            const someDeletable = details && deletable > 0 && deletable < total;
            const notice = deleteNotices[t];
            return (
              <div key={t} className="list-row">
                <div>
                  <div className="title-row">
                    <span className="title">{t}</span>
                    {details && <span className="chip">{details.count} daily bars</span>}
                    {noneDeletable && (
                      <span
                        className="chip chip-danger"
                        title="This ticker's data was imported by another user — you can't delete it."
                      >
                        Not yours
                      </span>
                    )}
                    {someDeletable && (
                      <span
                        className="chip chip-warn"
                        title={`${deletable} of ${total} bar(s) were imported by you; the rest belong to another user.`}
                      >
                        Partly yours
                      </span>
                    )}
                  </div>
                  {details && (
                    <p className="muted">
                      {details.start_date?.slice(0, 10)} to {details.end_date?.slice(0, 10)}
                    </p>
                  )}
                  {notice && <p className="muted">{notice}</p>}
                </div>
                <button
                  className="ghost-btn"
                  onClick={() => handleDeleteTicker(t)}
                  disabled={deletingTicker === t || noneDeletable}
                  title={noneDeletable ? "This ticker's data was imported by another user — you can't delete it." : undefined}
                >
                  {deletingTicker === t ? 'Deleting...' : 'Delete'}
                </button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <h3>Visualize</h3>
        {!tickersLoading && !tickers.length && <p className="muted">Import some data first.</p>}
        {!!tickers.length && (
          <>
            <div className="stack" style={{ flexDirection: 'row', gap: '12px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <label className="field">
                <span>Ticker</span>
                <select value={chartTicker} onChange={(e) => setChartTicker(e.target.value)}>
                  {tickers.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Start Date</span>
                <input
                  type="date"
                  value={chartRange.startDate}
                  min={tickerDetails[chartTicker]?.start_date?.slice(0, 10)}
                  max={tickerDetails[chartTicker]?.end_date?.slice(0, 10)}
                  onChange={(e) => setChartRange({ ...chartRange, startDate: e.target.value })}
                />
              </label>
              <label className="field">
                <span>End Date</span>
                <input
                  type="date"
                  value={chartRange.endDate}
                  min={tickerDetails[chartTicker]?.start_date?.slice(0, 10)}
                  max={tickerDetails[chartTicker]?.end_date?.slice(0, 10)}
                  onChange={(e) => setChartRange({ ...chartRange, endDate: e.target.value })}
                />
              </label>
              <button
                className="primary-btn"
                type="button"
                onClick={handleUpdateCharts}
                disabled={!chartRangeDirty || !chartRange.startDate || !chartRange.endDate}
              >
                Update Charts
              </button>
            </div>
            {chartLoading && <p className="muted" style={{ marginTop: '12px' }}>Loading chart...</p>}
            {chartError && <div className="error-box" style={{ marginTop: '12px' }}>{chartError}</div>}
            {!chartLoading && !chartError && (
              <div style={{ marginTop: '12px' }}>
                <MarketDataChart data={chartData} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

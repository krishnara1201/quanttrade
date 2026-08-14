import client from './client.js';

export async function getTickers() {
  const { data } = await client.get('/api/data/tickers');
  return data;
}

export async function getTickerRange(ticker) {
  const { data } = await client.get(`/api/data/${ticker}/range`);
  return data;
}

export async function getHistoricalData(ticker, startDate, endDate) {
  const { data } = await client.get(`/api/data/${ticker}/historical`, {
    params: {
      start_date: startDate || undefined,
      end_date: endDate || undefined,
    },
  });
  return data;
}

export async function uploadMarketDataCsv(ticker, file) {
  const formData = new FormData();
  if (ticker) formData.append('ticker', ticker);
  formData.append('file', file);
  const { data } = await client.post('/api/data/upload-csv', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

export async function importMarketDataFromWeb(ticker, startDate, endDate, apiKey) {
  const { data } = await client.post(`/api/data/import/${ticker}`, null, {
    params: {
      start_date: startDate || undefined,
      end_date: endDate || undefined,
      api_key: apiKey || undefined,
    },
  });
  return data;
}

export async function getImportJob(jobId) {
  const { data } = await client.get(`/api/data/jobs/${jobId}`);
  return data;
}

export async function deleteTickerData(ticker) {
  const { data } = await client.delete(`/api/data/${ticker}/all`);
  return data;
}

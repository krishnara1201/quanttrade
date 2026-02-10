import client from './client.js';

export async function runBacktest(strategyId, ticker, startDate, endDate, initialCapital = 10000) {
  const { data } = await client.post('/api/backtest/run', {
    strategy_id: strategyId,
    ticker,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
  });
  return data;
}

export async function getBacktestResults(strategyId) {
  const { data } = await client.get(`/api/backtest/results/${strategyId}`);
  return data;
}

export async function getBacktestDetail(backtestId) {
  const { data } = await client.get(`/api/backtest/${backtestId}`);
  return data;
}

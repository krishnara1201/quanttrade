import client from './client.js';

export async function runBacktest(strategyId, ticker, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05, allowShort = false, stopLossPct = null, takeProfitPct = null) {
  const { data } = await client.post('/api/backtest/run', {
    strategy_id: strategyId,
    ticker,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
    allow_short: allowShort,
    stop_loss_pct: stopLossPct,
    take_profit_pct: takeProfitPct,
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

export async function runPortfolioBacktest(strategyId, tickers, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05, allowShort = false, stopLossPct = null, takeProfitPct = null) {
  const { data } = await client.post('/api/backtest/run-portfolio', {
    strategy_id: strategyId,
    tickers,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
    allow_short: allowShort,
    stop_loss_pct: stopLossPct,
    take_profit_pct: takeProfitPct,
  });
  return data;
}

export async function getPortfolioBacktestResults(strategyId) {
  const { data } = await client.get(`/api/backtest/portfolio/results/${strategyId}`);
  return data;
}

export async function getPortfolioBacktestDetail(portfolioBacktestId) {
  const { data } = await client.get(`/api/backtest/portfolio/${portfolioBacktestId}`);
  return data;
}

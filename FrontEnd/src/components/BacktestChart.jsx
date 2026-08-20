import React from 'react';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ComposedChart
} from 'recharts';
import useIsMobile from '../hooks/useIsMobile';

function formatDateTick(value, isMobile) {
  const date = typeof value === 'string' ? value.split('T')[0] : value;
  return isMobile && typeof date === 'string' ? date.slice(5) : date;
}

export default function BacktestChart({
  data, trades, equityCurve, benchmarkEquityCurve = [], priceName = 'Stock Price', equityName = 'Equity',
}) {
  const isMobile = useIsMobile();
  const maxTicks = isMobile ? 4 : 10;
  const tickFontSize = isMobile ? 10 : 12;
  const entryByDate = new Map();
  const exitByDate = new Map();
  (trades || []).forEach((t) => {
    if (t.type === 'entry') entryByDate.set(t.date, t);
    else if (t.type === 'exit') exitByDate.set(t.date, t);
  });

  const chartData = (data || []).map((d) => {
    const entry = entryByDate.get(d.date) || null;
    const exit = exitByDate.get(d.date) || null;
    const isShortEntry = entry && entry.direction === 'short';
    const isShortExit = exit && exit.direction === 'short';
    return {
      date: d.date,
      close: d.close,
      longEntry: entry && !isShortEntry ? entry.price : null,
      shortEntry: isShortEntry ? entry.price : null,
      longExit: exit && !isShortExit ? exit.price : null,
      shortExit: isShortExit ? exit.price : null,
      pnl: exit ? exit.pnl : null,
    };
  });

  const benchmarkByDate = new Map((benchmarkEquityCurve || []).map((p) => [p.date, p.equity]));
  const hasBenchmark = (benchmarkEquityCurve || []).length > 0;
  const equityData = (equityCurve || []).map((point) => ({
    date: point.date,
    equity: point.equity,
    benchmark: benchmarkByDate.has(point.date) ? benchmarkByDate.get(point.date) : null,
  }));

  if (chartData.length === 0 && equityData.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  return (
    <div className="chart-container">
      {chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={400}>
          <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(value) => formatDateTick(value, isMobile)}
              tick={{ fontSize: tickFontSize }}
              interval={Math.floor(chartData.length / maxTicks)}
            />
            <YAxis yAxisId="left" />
            <YAxis yAxisId="right" orientation="right" />
            <Tooltip
              formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
              labelFormatter={formatDateTick}
              contentStyle={{ backgroundColor: '#10141f', border: '1px solid rgba(255, 255, 255, 0.08)' }}
              labelStyle={{ color: '#f5f7fb' }}
            />
            <Legend />

            <Line
              yAxisId="left"
              type="monotone"
              dataKey="close"
              stroke="#5da2ff"
              name={priceName}
              dot={false}
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="longEntry"
              fill="#7cf2d4"
              name="Long Entry"
              shape="triangle"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="shortEntry"
              fill="#ffb86c"
              name="Short Entry"
              shape="wye"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="longExit"
              fill="#ff6b6b"
              name="Long Exit"
              shape="diamond"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="shortExit"
              fill="#ff4da6"
              name="Short Exit"
              shape="square"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {equityData.length > 0 && (
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={equityData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={(value) => formatDateTick(value, isMobile)}
              tick={{ fontSize: tickFontSize }}
              interval={Math.floor(equityData.length / maxTicks)}
            />
            <YAxis />
            <Tooltip
              formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
              labelFormatter={formatDateTick}
              contentStyle={{ backgroundColor: '#10141f', border: '1px solid rgba(255, 255, 255, 0.08)' }}
              labelStyle={{ color: '#f5f7fb' }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="equity"
              stroke="#c792ea"
              name={equityName}
              dot={false}
              isAnimationActive={false}
            />
            {hasBenchmark && (
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke="#5da2ff"
                strokeDasharray="4 4"
                name="Buy & Hold"
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

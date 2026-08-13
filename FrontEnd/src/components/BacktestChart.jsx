import React from 'react';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ComposedChart
} from 'recharts';

function formatDateTick(value) {
  return typeof value === 'string' ? value.split('T')[0] : value;
}

export default function BacktestChart({ data, trades, equityCurve }) {
  if (!data || data.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  const tradesByDate = new Map();
  (trades || []).forEach((t) => {
    tradesByDate.set(t.date, t);
  });

  const chartData = data.map((d) => {
    const trade = tradesByDate.get(d.date);
    const entry = trade && trade.type === 'entry' ? trade : null;
    const exit = trade && trade.type === 'exit' ? trade : null;
    return {
      date: d.date,
      close: d.close,
      entry: entry ? entry.price : null,
      exit: exit ? exit.price : null,
      pnl: exit ? exit.pnl : null,
    };
  });

  const equityData = (equityCurve || []).map((point) => ({
    date: point.date,
    equity: point.equity,
  }));

  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tickFormatter={formatDateTick}
            tick={{ fontSize: 12 }}
            interval={Math.floor(chartData.length / 10)}
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
            name="Stock Price"
            dot={false}
            isAnimationActive={false}
          />

          <Scatter
            yAxisId="left"
            dataKey="entry"
            fill="#7cf2d4"
            name="Entry (Buy)"
            shape="triangle"
            isAnimationActive={false}
          />

          <Scatter
            yAxisId="left"
            dataKey="exit"
            fill="#ff6b6b"
            name="Exit (Sell)"
            shape="diamond"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {equityData.length > 0 && (
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={equityData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDateTick}
              tick={{ fontSize: 12 }}
              interval={Math.floor(equityData.length / 10)}
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
              name="Equity"
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

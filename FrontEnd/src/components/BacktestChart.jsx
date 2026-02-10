import React from 'react';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ComposedChart, ReferenceDot
} from 'recharts';

export default function BacktestChart({ data, trades }) {
  if (!data || data.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  // Prepare chart data by merging price data with entry/exit markers
  const chartData = data.map((d, idx) => {
    const entry = trades.find(t => t.type === 'entry' && t.index === idx);
    const exit = trades.find(t => t.type === 'exit' && t.index === idx);
    return {
      index: idx,
      date: d.date,
      close: d.close,
      entry: entry ? entry.price : null,
      exit: exit ? exit.price : null,
      pnl: exit ? exit.pnl : null,
    };
  });

  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 12 }}
            interval={Math.floor(chartData.length / 10)}
          />
          <YAxis yAxisId="left" />
          <YAxis yAxisId="right" orientation="right" />
          <Tooltip
            formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
            labelFormatter={(label) => `Index: ${label}`}
          />
          <Legend />

          {/* Stock Price Line */}
          <Line
            yAxisId="left"
            type="monotone"
            dataKey="close"
            stroke="#5da2ff"
            name="Stock Price"
            dot={false}
            isAnimationActive={false}
          />

          {/* Entry Points (Buy) */}
          <Scatter
            yAxisId="left"
            dataKey="entry"
            fill="#7cf2d4"
            name="Entry (Buy)"
            shape="triangle"
            isAnimationActive={false}
          />

          {/* Exit Points (Sell) */}
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
    </div>
  );
}

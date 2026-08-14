import React from 'react';
import {
  ComposedChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
} from 'recharts';

const UP_COLOR = '#3ecf8e';
const DOWN_COLOR = '#ff6b6b';

function formatDateTick(value) {
  return typeof value === 'string' ? value.split('T')[0] : value;
}

function Candlestick(props) {
  const { x, y, width, height, payload } = props;
  const { open, close, high, low } = payload;
  if (high === low) return null;

  const color = close >= open ? UP_COLOR : DOWN_COLOR;
  const scaleY = (value) => y + (height * (high - value)) / (high - low);
  const openY = scaleY(open);
  const closeY = scaleY(close);
  const bodyTop = Math.min(openY, closeY);
  const bodyHeight = Math.max(Math.abs(closeY - openY), 1);
  const bodyWidth = Math.max(width * 0.6, 1);
  const bodyX = x + (width - bodyWidth) / 2;
  const wickX = x + width / 2;

  return (
    <g>
      <line x1={wickX} x2={wickX} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={bodyX} y={bodyTop} width={bodyWidth} height={bodyHeight} fill={color} />
    </g>
  );
}

function VolumeBar(props) {
  const { x, y, width, height, payload } = props;
  const color = payload.close >= payload.open ? UP_COLOR : DOWN_COLOR;
  return <rect x={x} y={y} width={width} height={height} fill={color} fillOpacity={0.5} />;
}

function CandlestickTooltip({ active, payload, label }) {
  if (!active || !payload || !payload.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{
      backgroundColor: '#10141f',
      border: '1px solid rgba(255, 255, 255, 0.08)',
      padding: '8px 10px',
      color: '#f5f7fb',
      fontSize: 12,
    }}>
      <div style={{ marginBottom: 4 }}>{formatDateTick(label)}</div>
      <div>Open: {d.open.toFixed(2)}</div>
      <div>High: {d.high.toFixed(2)}</div>
      <div>Low: {d.low.toFixed(2)}</div>
      <div>Close: {d.close.toFixed(2)}</div>
      <div>Volume: {d.volume.toLocaleString()}</div>
    </div>
  );
}

export default function MarketDataChart({ data }) {
  if (!data || data.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  const chartData = data.map((d) => ({
    date: d.date,
    open: Number(d.open),
    high: Number(d.high),
    low: Number(d.low),
    close: Number(d.close),
    volume: Number(d.volume),
  }));

  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" tickFormatter={formatDateTick} tick={{ fontSize: 12 }} interval={Math.floor(chartData.length / 10)} />
          <YAxis domain={['auto', 'auto']} />
          <Tooltip content={<CandlestickTooltip />} />
          <Bar dataKey={(d) => [d.low, d.high]} shape={Candlestick} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>

      <ResponsiveContainer width="100%" height={130}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" tickFormatter={formatDateTick} tick={{ fontSize: 12 }} interval={Math.floor(chartData.length / 10)} />
          <YAxis />
          <Tooltip content={<CandlestickTooltip />} />
          <Bar dataKey="volume" shape={VolumeBar} isAnimationActive={false} name="Volume" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

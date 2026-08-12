import React, { useState } from 'react';

const INDICATORS = [
  { id: 'sma', name: 'Simple Moving Average', params: ['period'] },
  { id: 'ema', name: 'Exponential Moving Average', params: ['period'] },
  { id: 'rsi', name: 'RSI', params: ['period'] },
  { id: 'bb', name: 'Bollinger Bands', params: ['period', 'std_dev'] },
  { id: 'macd', name: 'MACD', params: ['fast', 'slow', 'signal'] },
];

const COMPARISONS = [
  { id: '>', label: 'Greater than (>)' },
  { id: '<', label: 'Less than (<)' },
  { id: '>=', label: 'Greater or equal (>=)' },
  { id: '<=', label: 'Less or equal (<=)' },
];

const STRATEGY_TEMPLATES = [
  {
    name: 'Moving Average Crossover',
    description: 'Buy when fast MA crosses above slow MA, sell when it crosses below',
    config: {
      indicators: [
        { type: 'sma', name: 'fast_ma', period: 10 },
        { type: 'sma', name: 'slow_ma', period: 20 },
      ],
      entry: { left: 'fast_ma', operator: '>', right: 'slow_ma' },
      exit: { left: 'fast_ma', operator: '<', right: 'slow_ma' },
    },
  },
  {
    name: 'RSI Oversold/Overbought',
    description: 'Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought)',
    config: {
      indicators: [{ type: 'rsi', name: 'rsi', period: 14 }],
      entry: { left: 'rsi', operator: '<', right: '30' },
      exit: { left: 'rsi', operator: '>', right: '70' },
    },
  },
  {
    name: 'Bollinger Bands Bounce',
    description: 'Buy at lower band, sell at upper band',
    config: {
      indicators: [{ type: 'bb', name: 'bb', period: 20, std_dev: 2 }],
      entry: { left: 'close', operator: '<', right: 'bb_lower' },
      exit: { left: 'close', operator: '>', right: 'bb_upper' },
    },
  },
];

const CUSTOM_CODE_TEMPLATE = `def generate_signals(df):
    # df has columns: open, high, low, close, volume, indexed by date.
    # Return a pandas Series aligned to df.index with values:
    #   1  = enter a position
    #  -1  = exit the position
    #   0  = hold
    sma20 = df['close'].rolling(20).mean()
    return (df['close'] > sma20).astype(int)
`;

export default function StrategyBuilder({ onSave, onCancel }) {
  const [mode, setMode] = useState('rules');
  const [strategyName, setStrategyName] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [indicators, setIndicators] = useState([]);
  const [entryRule, setEntryRule] = useState({ left: '', operator: '>', right: '' });
  const [exitRule, setExitRule] = useState({ left: '', operator: '<', right: '' });
  const [code, setCode] = useState(CUSTOM_CODE_TEMPLATE);

  const applyTemplate = (template) => {
    setStrategyName(template.name);
    setIndicators(template.config.indicators);
    setEntryRule(template.config.entry);
    setExitRule(template.config.exit);
    setSelectedTemplate(template.name);
  };

  const addIndicator = () => {
    setIndicators([...indicators, { type: 'sma', name: '', period: 10 }]);
  };

  const updateIndicator = (index, field, value) => {
    const updated = [...indicators];
    updated[index][field] = value;
    setIndicators(updated);
  };

  const removeIndicator = (index) => {
    setIndicators(indicators.filter((_, i) => i !== index));
  };

  const handleSave = () => {
    if (mode === 'custom_code') {
      onSave({ name: strategyName, mode: 'custom_code', code });
      return;
    }

    // Build parameters object
    const parameters = {};
    indicators.forEach((ind) => {
      if (ind.type === 'sma') {
        parameters[ind.name] = ind.period;
      } else if (ind.type === 'ema') {
        parameters.ema_period = ind.period;
      } else if (ind.type === 'rsi') {
        parameters.rsi_period = ind.period;
      } else if (ind.type === 'bb') {
        parameters.bb_period = ind.period;
        parameters.bb_std = ind.std_dev || 2;
      } else if (ind.type === 'macd') {
        parameters.macd_fast = ind.fast || 12;
        parameters.macd_slow = ind.slow || 26;
        parameters.macd_signal = ind.signal || 9;
      }
    });

    // Build rules
    const rules = {
      entry: `${entryRule.left} ${entryRule.operator} ${entryRule.right}`,
      exit: `${exitRule.left} ${exitRule.operator} ${exitRule.right}`,
    };

    onSave({ name: strategyName, mode: 'rules', parameters, rules });
  };

  const availableVariables = [
    'close',
    'open',
    'high',
    'low',
    ...indicators.map((i) => i.name).filter(Boolean),
    ...indicators.filter((i) => i.type === 'bb').map(() => ['bb_upper', 'bb_lower', 'bb_mid']).flat(),
    ...indicators.filter((i) => i.type === 'rsi').map(() => 'rsi'),
  ];

  const canSave = mode === 'custom_code'
    ? Boolean(strategyName && code.trim())
    : Boolean(strategyName);

  return (
    <div className="strategy-builder">
      <div className="builder-header">
        <h3>Strategy Builder</h3>
        <button className="ghost-btn" onClick={onCancel}>Cancel</button>
      </div>

      {/* Mode toggle */}
      <div className="section">
        <div className="mode-toggle">
          <button
            type="button"
            className={mode === 'rules' ? 'primary-btn' : 'ghost-btn'}
            onClick={() => setMode('rules')}
          >
            Visual Builder
          </button>
          <button
            type="button"
            className={mode === 'custom_code' ? 'primary-btn' : 'ghost-btn'}
            onClick={() => setMode('custom_code')}
          >
            Custom Python Code
          </button>
        </div>
      </div>

      {/* Strategy Name */}
      <div className="section">
        <label className="field">
          <span>Strategy Name</span>
          <input
            value={strategyName}
            onChange={(e) => setStrategyName(e.target.value)}
            placeholder="My Awesome Strategy"
            required
          />
        </label>
      </div>

      {mode === 'custom_code' ? (
        <div className="section">
          <h4>Custom Python Code</h4>
          <p className="muted">
            Define a <code>generate_signals(df)</code> function. Available columns: <code>open</code>,{' '}
            <code>high</code>, <code>low</code>, <code>close</code>, <code>volume</code>. <code>pd</code> and{' '}
            <code>np</code> are available; other imports aren't allowed. Code runs in a sandboxed process with a
            resource limit and a timeout, so keep it simple and avoid unbounded loops.
          </p>
          <textarea
            className="code-editor"
            rows={18}
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
      ) : (
        <>
          {/* Templates */}
          <div className="section">
            <h4>Start from a template</h4>
            <div className="template-grid">
              {STRATEGY_TEMPLATES.map((template) => (
                <div
                  key={template.name}
                  className={`template-card ${selectedTemplate === template.name ? 'selected' : ''}`}
                  onClick={() => applyTemplate(template)}
                >
                  <div className="template-name">{template.name}</div>
                  <p className="template-desc">{template.description}</p>
                </div>
              ))}
            </div>
          </div>

          {/* Indicators */}
          <div className="section">
            <div className="section-head">
              <h4>Indicators</h4>
              <button className="ghost-btn" onClick={addIndicator}>+ Add Indicator</button>
            </div>
            <div className="indicators-list">
              {indicators.map((indicator, idx) => (
                <div key={idx} className="indicator-row">
                  <select
                    value={indicator.type}
                    onChange={(e) => updateIndicator(idx, 'type', e.target.value)}
                  >
                    {INDICATORS.map((ind) => (
                      <option key={ind.id} value={ind.id}>{ind.name}</option>
                    ))}
                  </select>

                  <input
                    placeholder="Variable name (e.g., fast_ma)"
                    value={indicator.name}
                    onChange={(e) => updateIndicator(idx, 'name', e.target.value)}
                  />

                  {indicator.type === 'sma' && (
                    <input
                      type="number"
                      placeholder="Period"
                      value={indicator.period}
                      onChange={(e) => updateIndicator(idx, 'period', Number(e.target.value))}
                    />
                  )}

                  {indicator.type === 'ema' && (
                    <input
                      type="number"
                      placeholder="Period"
                      value={indicator.period}
                      onChange={(e) => updateIndicator(idx, 'period', Number(e.target.value))}
                    />
                  )}

                  {indicator.type === 'rsi' && (
                    <input
                      type="number"
                      placeholder="Period"
                      value={indicator.period}
                      onChange={(e) => updateIndicator(idx, 'period', Number(e.target.value))}
                    />
                  )}

                  {indicator.type === 'bb' && (
                    <>
                      <input
                        type="number"
                        placeholder="Period"
                        value={indicator.period}
                        onChange={(e) => updateIndicator(idx, 'period', Number(e.target.value))}
                      />
                      <input
                        type="number"
                        placeholder="Std Dev"
                        value={indicator.std_dev || 2}
                        onChange={(e) => updateIndicator(idx, 'std_dev', Number(e.target.value))}
                      />
                    </>
                  )}

                  <button className="icon-btn" onClick={() => removeIndicator(idx)}>🗑️</button>
                </div>
              ))}
              {indicators.length === 0 && <p className="muted">No indicators added yet. Click "Add Indicator" to start.</p>}
            </div>
          </div>

          {/* Entry Rule */}
          <div className="section">
            <h4>Entry Rule (When to Buy)</h4>
            <div className="rule-builder">
              <select
                value={entryRule.left}
                onChange={(e) => setEntryRule({ ...entryRule, left: e.target.value })}
              >
                <option value="">Select...</option>
                {availableVariables.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>

              <select
                value={entryRule.operator}
                onChange={(e) => setEntryRule({ ...entryRule, operator: e.target.value })}
              >
                {COMPARISONS.map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </select>

              <input
                placeholder="Value or variable"
                value={entryRule.right}
                onChange={(e) => setEntryRule({ ...entryRule, right: e.target.value })}
              />
            </div>
          </div>

          {/* Exit Rule */}
          <div className="section">
            <h4>Exit Rule (When to Sell)</h4>
            <div className="rule-builder">
              <select
                value={exitRule.left}
                onChange={(e) => setExitRule({ ...exitRule, left: e.target.value })}
              >
                <option value="">Select...</option>
                {availableVariables.map((v) => (
                  <option key={v} value={v}>{v}</option>
                ))}
              </select>

              <select
                value={exitRule.operator}
                onChange={(e) => setExitRule({ ...exitRule, operator: e.target.value })}
              >
                {COMPARISONS.map((c) => (
                  <option key={c.id} value={c.id}>{c.label}</option>
                ))}
              </select>

              <input
                placeholder="Value or variable"
                value={exitRule.right}
                onChange={(e) => setExitRule({ ...exitRule, right: e.target.value })}
              />
            </div>
          </div>
        </>
      )}

      {/* Actions */}
      <div className="builder-actions">
        <button className="ghost-btn" onClick={onCancel}>Cancel</button>
        <button className="primary-btn" onClick={handleSave} disabled={!canSave}>
          Save Strategy
        </button>
      </div>
    </div>
  );
}

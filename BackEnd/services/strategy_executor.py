"""
Strategy Executor - Interprets and runs user-defined strategies
Supports JSON-based configuration approach for safety
"""
import json
import pandas as pd
import numpy as np
from typing import Any, Dict, List
from datetime import datetime

# Available indicators and rules
AVAILABLE_INDICATORS = {
    'sma': 'Simple Moving Average',
    'ema': 'Exponential Moving Average',
    'rsi': 'Relative Strength Index',
    'bb': 'Bollinger Bands',
    'macd': 'MACD',
}

class StrategyExecutor:
    """Executes a strategy on historical market data"""
    
    def __init__(self, strategy_config: str, code: str = None):
        """
        Args:
            strategy_config: JSON string (or dict) with strategy definition
            code: Python source for a 'custom_code' mode strategy (see
                config['mode']); a generate_signals(df) function evaluated
                in services.sandbox_executor in place of _calculate_indicators
                + the entry/exit condition loop.
        """
        try:
            self.config = json.loads(strategy_config) if isinstance(strategy_config, str) else strategy_config
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid strategy configuration: {e}")
        self.code = code

    def validate(self) -> bool:
        """Validate strategy configuration"""
        if 'name' not in self.config:
            raise ValueError("Missing required field: name")

        mode = self.config.get('mode', 'rules')
        if mode == 'custom_code':
            if not self.code or not self.code.strip():
                raise ValueError("Custom-code strategy requires non-empty 'code'")
        elif mode == 'rules':
            if 'parameters' not in self.config:
                raise ValueError("Missing required field: parameters")
            rules = self.config.get('rules', {})
            if 'entry' not in rules or 'exit' not in rules:
                raise ValueError("Strategy must define 'entry' and 'exit' rules")
        else:
            raise ValueError(f"Unknown strategy mode: {mode!r}")

        return True
    
    def backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0,
                 commission_pct: float = 0.1, slippage_pct: float = 0.05) -> Dict[str, Any]:
        """
        Run backtest on market data

        Args:
            df: DataFrame with OHLCV data (must have 'close' column)
            initial_capital: Starting capital
            commission_pct: Commission as a percent of trade notional
            slippage_pct: Slippage as a percent applied against the trader on fill

        Returns:
            Results dict with trades, metrics, per-bar signals, and equity curve
        """
        self.validate()

        df = df.copy()
        mode = self.config.get('mode', 'rules')

        if mode == 'custom_code':
            from services.sandbox_executor import run_custom_strategy, SandboxError
            try:
                df['signal'] = run_custom_strategy(self.code, df)
            except SandboxError as e:
                raise ValueError(str(e))
        else:
            params = self.config.get('parameters', {})
            rules = self.config.get('rules', {})

            self._calculate_indicators(df, params)

            df['signal'] = 0

            for i in range(1, len(df)):
                if self._evaluate_condition(rules['entry'], df, i):
                    df.iloc[i, df.columns.get_loc('signal')] = 1
                elif self._evaluate_condition(rules['exit'], df, i):
                    df.iloc[i, df.columns.get_loc('signal')] = -1

        trades, equity_curve = self._execute_trades(df, initial_capital, commission_pct, slippage_pct)
        metrics = self._calculate_metrics(df, trades, initial_capital, equity_curve)

        signals = [
            {
                'date': self._format_date(df.index[i]),
                'close': float(df.iloc[i]['close']),
                'signal': int(df.iloc[i]['signal']),
            }
            for i in range(len(df))
        ]

        return {
            'trades': trades,
            'metrics': metrics,
            'signals': signals,
            'equity_curve': equity_curve,
        }
    
    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Exponential moving average, matching pandas' standard ewm formula"""
        return series.ewm(span=period, adjust=False).mean()

    def _calculate_indicators(self, df: pd.DataFrame, params: Dict[str, Any]):
        """Calculate technical indicators based on strategy parameters"""

        # Simple Moving Average
        if 'fast_ma' in params:
            df['fast_ma'] = df['close'].rolling(window=int(params['fast_ma'])).mean()
        if 'slow_ma' in params:
            df['slow_ma'] = df['close'].rolling(window=int(params['slow_ma'])).mean()

        # RSI (Relative Strength Index)
        if 'rsi_period' in params:
            period = int(params['rsi_period'])
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        if 'bb_period' in params and 'bb_std' in params:
            period = int(params['bb_period'])
            std_dev = float(params['bb_std'])
            df['bb_mid'] = df['close'].rolling(window=period).mean()
            df['bb_std'] = df['close'].rolling(window=period).std()
            df['bb_upper'] = df['bb_mid'] + (std_dev * df['bb_std'])
            df['bb_lower'] = df['bb_mid'] - (std_dev * df['bb_std'])

        # Exponential Moving Average
        if 'ema_period' in params:
            df['ema'] = self._calculate_ema(df['close'], int(params['ema_period']))

        # MACD
        if 'macd_fast' in params and 'macd_slow' in params and 'macd_signal' in params:
            fast_ema = self._calculate_ema(df['close'], int(params['macd_fast']))
            slow_ema = self._calculate_ema(df['close'], int(params['macd_slow']))
            df['macd'] = fast_ema - slow_ema
            df['macd_signal_line'] = self._calculate_ema(df['macd'], int(params['macd_signal']))
            df['macd_hist'] = df['macd'] - df['macd_signal_line']
    
    def _evaluate_condition(self, condition: str, df: pd.DataFrame, row_idx: int) -> bool:
        """
        Safely evaluate a condition string against current row
        Examples: "fast_ma > slow_ma", "rsi < 30", "close > bb_upper"
        """
        import re
        try:
            # Whitelist: only allow column names, numbers, and comparison operators
            # This prevents code injection attacks
            allowed_pattern = r'^[\w\s\.\d]+\s*[<>=!]+\s*[\w\s\.\d]+$'
            if not re.match(allowed_pattern, condition):
                print(f"Invalid condition format: {condition}")
                return False
            
            # Parse the condition manually instead of using eval
            operators = {
                '>': lambda a, b: a > b,
                '<': lambda a, b: a < b,
                '>=': lambda a, b: a >= b,
                '<=': lambda a, b: a <= b,
                '==': lambda a, b: a == b,
                '!=': lambda a, b: a != b,
            }
            
            # Find the operator
            op = None
            for operator_symbol in ['>=', '<=', '==', '!=', '>', '<']:
                if operator_symbol in condition:
                    op = operator_symbol
                    break
            
            if not op:
                return False
            
            left, right = condition.split(op)
            left, right = left.strip(), right.strip()
            
            # Get values from dataframe or parse as number
            def get_value(val_str):
                if val_str in df.columns:
                    return df.iloc[row_idx][val_str]
                try:
                    return float(val_str)
                except ValueError:
                    raise ValueError(f"Unknown variable or invalid number: {val_str}")
            
            left_val = get_value(left)
            right_val = get_value(right)
            
            return operators[op](left_val, right_val)
        except Exception as e:
            print(f"Error evaluating condition '{condition}': {e}")
            return False
    
    def _format_date(self, idx) -> str:
        return idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)

    def _execute_trades(self, df: pd.DataFrame, initial_capital: float,
                         commission_pct: float = 0.1, slippage_pct: float = 0.05):
        """Execute trades based on signals using capital-based sizing with commission/slippage"""
        trades = []
        equity_curve = []
        cash = initial_capital
        shares = 0
        entry_cost_basis = 0.0

        for i in range(len(df)):
            signal = df['signal'].iloc[i] if 'signal' in df.columns else 0
            close_price = df['close'].iloc[i]
            timestamp = self._format_date(df.index[i])

            if signal == 1 and shares == 0:
                fill_price = close_price * (1 + slippage_pct / 100)
                effective_price = fill_price * (1 + commission_pct / 100)
                candidate_shares = int(cash // effective_price)
                if candidate_shares > 0:
                    commission = candidate_shares * fill_price * (commission_pct / 100)
                    cost = candidate_shares * fill_price + commission
                    shares = candidate_shares
                    cash -= cost
                    entry_cost_basis = cost
                    trades.append({
                        'type': 'entry',
                        'price': float(fill_price),
                        'date': timestamp,
                        'size': shares,
                    })

            elif signal == -1 and shares > 0:
                fill_price = close_price * (1 - slippage_pct / 100)
                proceeds = shares * fill_price
                commission = proceeds * (commission_pct / 100)
                net_proceeds = proceeds - commission
                pnl = net_proceeds - entry_cost_basis
                cash += net_proceeds
                trades.append({
                    'type': 'exit',
                    'price': float(fill_price),
                    'date': timestamp,
                    'size': shares,
                    'pnl': float(pnl),
                })
                shares = 0
                entry_cost_basis = 0.0

            equity = cash + (shares * close_price if shares > 0 else 0)
            equity_curve.append({'date': timestamp, 'equity': float(equity)})

        return trades, equity_curve
    
    def _calculate_metrics(self, df: pd.DataFrame, trades: List[Dict],
                            initial_capital: float, equity_curve: List[Dict]) -> Dict[str, Any]:
        """Calculate performance metrics"""
        max_drawdown_pct = self._max_drawdown_pct(equity_curve)
        sharpe_ratio = self._sharpe_ratio(equity_curve)

        if not trades:
            return {
                'total_return': 0.0,
                'return_pct': 0.0,
                'win_rate': 0.0,
                'num_trades': 0,
                'max_drawdown_pct': max_drawdown_pct,
                'sharpe_ratio': sharpe_ratio,
            }

        total_pnl = sum(t.get('pnl', 0) for t in trades if t['type'] == 'exit')

        if equity_curve:
            final_capital = equity_curve[-1]['equity']
        else:
            final_capital = initial_capital + total_pnl
        total_return = final_capital - initial_capital
        return_pct = (total_return / initial_capital) * 100

        exits = [t for t in trades if t['type'] == 'exit']
        wins = len([t for t in exits if t.get('pnl', 0) > 0])
        win_rate = (wins / len(exits) * 100) if exits else 0.0

        return {
            'total_return': float(total_return),
            'return_pct': float(return_pct),
            'win_rate': float(win_rate),
            'num_trades': len(exits),
            'final_capital': float(final_capital),
            'max_drawdown_pct': max_drawdown_pct,
            'sharpe_ratio': sharpe_ratio,
        }

    def _max_drawdown_pct(self, equity_curve: List[Dict]) -> float:
        if not equity_curve:
            return 0.0
        peak = equity_curve[0]['equity']
        max_dd = 0.0
        for point in equity_curve:
            equity = point['equity']
            peak = max(peak, equity)
            if peak > 0:
                drawdown = (peak - equity) / peak * 100
                max_dd = max(max_dd, drawdown)
        return float(max_dd)

    def _sharpe_ratio(self, equity_curve: List[Dict]) -> float:
        if len(equity_curve) < 2:
            return 0.0
        values = pd.Series([p['equity'] for p in equity_curve])
        returns = values.pct_change().dropna()
        std = returns.std()
        if not std or pd.isna(std) or std == 0:
            return 0.0
        return float((returns.mean() / std) * (252 ** 0.5))

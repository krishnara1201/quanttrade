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
    
    def __init__(self, strategy_config: str):
        """
        Args:
            strategy_config: JSON string with strategy definition
        """
        try:
            self.config = json.loads(strategy_config) if isinstance(strategy_config, str) else strategy_config
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid strategy configuration: {e}")
    
    def validate(self) -> bool:
        """Validate strategy configuration"""
        required_fields = ['name', 'parameters', 'rules']
        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required field: {field}")
        
        rules = self.config.get('rules', {})
        if 'entry' not in rules or 'exit' not in rules:
            raise ValueError("Strategy must define 'entry' and 'exit' rules")
        
        return True
    
    def backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0) -> Dict[str, Any]:
        """
        Run backtest on market data
        
        Args:
            df: DataFrame with OHLCV data (must have 'close' column)
            initial_capital: Starting capital
            
        Returns:
            Results dict with trades, metrics, performance
        """
        self.validate()
        
        # Make a copy to avoid modifying original
        df = df.copy()
        params = self.config.get('parameters', {})
        rules = self.config.get('rules', {})
        
        # Calculate indicators based on parameters
        self._calculate_indicators(df, params)
        
        # Generate signals
        df['signal'] = 0  # 0=hold, 1=buy, -1=sell
        
        for i in range(1, len(df)):
            # Evaluate entry condition
            if self._evaluate_condition(rules['entry'], df, i):
                df.loc[i, 'signal'] = 1
            
            # Evaluate exit condition
            elif self._evaluate_condition(rules['exit'], df, i):
                df.loc[i, 'signal'] = -1
        
        # Execute trades and calculate P&L
        trades = self._execute_trades(df, initial_capital)
        metrics = self._calculate_metrics(df, trades, initial_capital)
        
        return {
            'trades': trades,
            'metrics': metrics,
            'signals': df[['close', 'signal']].to_dict(orient='list'),
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
    
    def _execute_trades(self, df: pd.DataFrame, initial_capital: float) -> List[Dict[str, Any]]:
        """Execute trades based on signals"""
        trades = []
        position = None  # None or entry_price
        
        for i in range(len(df)):
            signal = df.iloc[i].get('signal', 0)
            close_price = df.iloc[i]['close']
            timestamp = df.index[i] if hasattr(df.index[i], 'isoformat') else str(df.index[i])
            
            # Buy signal
            if signal == 1 and position is None:
                position = close_price
                trades.append({
                    'type': 'entry',
                    'price': float(close_price),
                    'date': timestamp,
                    'size': 1,  # 1 share for simplicity
                })
            
            # Sell signal
            elif signal == -1 and position is not None:
                trades.append({
                    'type': 'exit',
                    'price': float(close_price),
                    'date': timestamp,
                    'size': 1,
                    'pnl': float((close_price - position) * 1),
                })
                position = None
        
        return trades
    
    def _calculate_metrics(self, df: pd.DataFrame, trades: List[Dict], initial_capital: float) -> Dict[str, Any]:
        """Calculate performance metrics"""
        if not trades:
            return {
                'total_return': 0.0,
                'return_pct': 0.0,
                'win_rate': 0.0,
                'num_trades': 0,
                'max_drawdown': 0.0,
            }
        
        # Calculate total P&L
        total_pnl = sum(t.get('pnl', 0) for t in trades if t['type'] == 'exit')
        
        # Calculate return
        final_capital = initial_capital + total_pnl
        total_return = final_capital - initial_capital
        return_pct = (total_return / initial_capital) * 100
        
        # Calculate win rate
        exits = [t for t in trades if t['type'] == 'exit']
        wins = len([t for t in exits if t.get('pnl', 0) > 0])
        win_rate = (wins / len(exits) * 100) if exits else 0.0
        
        return {
            'total_return': float(total_return),
            'return_pct': float(return_pct),
            'win_rate': float(win_rate),
            'num_trades': len(exits),
            'final_capital': float(final_capital),
        }

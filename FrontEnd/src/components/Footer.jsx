import React from 'react';
import { ChartCandlestick, Scale, SquareCode } from 'lucide-react';

export default function Footer() {
  return (
    <footer className="site-footer">
      <div className="footer-inner">
        <div className="brand-mark">
          <ChartCandlestick size={20} />
          <span>QuantTrade</span>
        </div>
        <p className="footer-tagline">
          A backtesting platform for trading strategies — build, test, and review before you risk anything real.
        </p>
        <div className="footer-links">
          <a href="https://github.com/krishnara1201/quanttrade" target="_blank" rel="noreferrer">
            <SquareCode size={15} />
            <span>Source on GitHub</span>
          </a>
          <a href="https://github.com/krishnara1201/quanttrade/blob/main/LICENSE" target="_blank" rel="noreferrer">
            <Scale size={15} />
            <span>MIT License</span>
          </a>
        </div>
      </div>
    </footer>
  );
}

# Pre-login Landing Page: How It Works / Features / FAQ

## Context

`FrontEnd/src/pages/MainPage.jsx` is the `/` route, shown to both logged-out and logged-in visitors (`App.jsx` routes it with no `ProtectedRoute`). It's currently a single hero section — headline, CTA buttons, and 3 small highlight cards (`REST Backed`, `Stateful Auth`, `Project Focused`) — with no explanation of the product's actual workflow or feature set. A first-time visitor gets no sense of what a "strategy" is, how backtesting works, or that this is a historical-data backtester rather than a live-trading tool.

## Goal

Add a compact informational section below the existing hero that explains the product in more depth, without turning the page into a long scroll.

## Structure

The hero section is unchanged. A new `<section className="info-section">` is added directly below it, containing:

- A 3-tab bar: **How it works**, **Features**, **FAQ**. Tab state is local `useState('how')` in `MainPage.jsx` — no routing, no URL/query-param sync, no new global state.
- Tab bar reuses the existing `.tab` / `.tab.active` classes from `styles.css` for individual tab buttons, but under a new `.info-tabs` container class (a 3-column grid), rather than reusing `.tabs` (a 2-column grid hardcoded for `AuthPage.jsx`'s login/register toggle). `.tabs` is left untouched.
- Only the active tab's panel renders below the tab bar.

### How it works (tab 1)

Five numbered steps, rendered as an ordered list (new `.steps` / `.step` classes, styled consistent with `.list`/`.list-row`):

1. **Create a project** — organize strategies by project.
2. **Import market data** — upload a CSV of OHLCV bars, or import daily history from Alpha Vantage.
3. **Build a strategy** — use the visual rule builder (indicators + entry/exit conditions) or write custom Python signal logic.
4. **Run a backtest** — pick a ticker, date range, and starting capital.
5. **Review results** — trade log, equity curve, and performance metrics.

### Features (tab 2)

A card grid reusing `.grid-preview` / `.card`:

- **Visual strategy builder** — SMA, EMA, RSI, Bollinger Bands, and MACD indicators, combined into entry/exit rules with no code required.
- **Custom Python strategies** — write your own signal logic in Python for full control; runs in a restricted, isolated environment.
- **Market data import** — bring your own CSV, or pull historical daily bars from Alpha Vantage.
- **Backtest metrics** — total return, win rate, Sharpe ratio, max drawdown, and a full trade-by-trade log.
- **REST API-backed** — every action goes through a documented FastAPI backend.
- **Secure by default** — JWT-authenticated, project-scoped access to your strategies and results.

### FAQ (tab 3)

Q&A list reusing `.list` / `.list-row`:

- **Does this place real trades?** No — QuantTrade is a backtesting platform. It simulates strategies against historical market data; it does not connect to a broker or execute live trades.
- **Do I need to know how to code?** No — the visual builder covers indicator-based strategies with no code. Custom Python is available if you want more control.
- **Is my custom strategy code safe to run?** Custom code executes in a restricted, isolated environment that only permits pandas/numpy operations — it can't access the network or file system.
- **Where does market data come from?** Upload your own CSV files, or import daily historical bars from Alpha Vantage directly in the app.
- **What does a backtest show me?** A full trade log, an equity curve, and summary metrics (return, win rate, Sharpe ratio, max drawdown).

## Content depth

Product-level throughout: what each feature does and why it's useful, not implementation internals (no mention of subprocess sandboxing, AST checks, resource limits, etc. — those live in `CLAUDE.md` for contributors, not end users).

## Testing

No frontend test suite exists for `FrontEnd/` (per `CLAUDE.md`), and this change doesn't introduce one. Verified by hand via the Vite dev server:
- All three tabs render their expected content and switching between them works.
- The hero section and its existing CTAs are unaffected.
- `AuthPage.jsx`'s existing `.tabs` (login/register toggle) still renders as a 2-column grid, confirming the new `.info-tabs` class didn't affect the shared `.tab` styles.

## Explicitly out of scope

- Any change to `AuthPage.jsx`, `TopNav.jsx`, or any other page.
- Screenshots, diagrams, or other media assets — text content only.
- Accordion-style layout (considered and rejected in favor of tabs, per user preference).
- Persisting the selected tab (e.g. in the URL) across navigation/reload.

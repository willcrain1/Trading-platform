# Trading Analysis Platform

A local web dashboard for analyzing tickers using the methodologies Nicholas Crown
(ex-UBS fixed income arbitrage trader, author of The Crown Macro Letter) discusses
publicly: technical indicators, cross-asset macro context, dealer gamma positioning,
and relative value spreads — plus backtesting of the indicator strategies.

**Educational analysis tool only.** It places no orders, connects to no broker, and
nothing it shows is investment advice.

## Quick start

Backend (Python 3.11+):

```
cd backend
pip install -r requirements.txt
py -m uvicorn app.main:app --port 8000
```

Frontend (Node 18+):

```
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The Vite dev server proxies `/api` to the backend on :8000.

## Configuration (API keys)

Third-party API keys (Quiver Quantitative for congressional trading data, Polygon/Massive
as a fallback price data source) are read from `backend/data/app_config.json`, which is
git-ignored since it holds live secrets. Copy the template and fill in your own keys:

```
cp backend/data/app_config.example.json backend/data/app_config.json
```

Both keys can also be set from the Settings page in the UI (writes to the same file), or
via the `POLYGON_API_KEY` / `MASSIVE_API_KEY` env vars for the Polygon fallback. Neither
key is required — the app runs fine without them, just without congressional-trading data
or the Polygon fallback for cached-Yahoo-outage scenarios.

## Pages

| Page | What it shows |
|---|---|
| **Ticker** | Candlestick chart with SMA/EMA/Bollinger overlays, swing-pivot support/resistance levels, RSI and MACD panes, and a distilled signal summary (trend, momentum, golden/death cross, Bollinger stretch, ATR). |
| **Macro** | Crown Macro Letter-style cross-asset table: equities, treasury yields + 10Y−3M curve, dollar index, crude/gold/copper, VIX, bitcoin — with 20-day sparklines and a risk-on/off composite. |
| **GEX** | Dealer gamma exposure by strike from the options chain (Black-Scholes gamma × OI × 100 × spot, calls +, puts −). Net GEX, zero-gamma flip point, max pain, put/call OI ratio, and the largest-|GEX| strikes as dealer-informed support/resistance. |
| **Rel Value** | Any-pair relative value: price ratio, rolling z-score of the log-spread (±2σ bands), rolling correlation, and mean-reversion half-life from an Ornstein-Uhlenbeck fit. Preset pairs included (QQQ/SPY, GLD/SLV, XLE/CL=F, HYG/TLT…). |
| **Backtest** | Long/flat daily-bar backtests — RSI mean reversion, SMA cross, MACD cross, Bollinger revert — with equity curve vs buy-and-hold, CAGR, Sharpe, max drawdown, win rate, time in market. Signals execute on the next close (no look-ahead). |
| **Paper** | Automated paper trading on a built-in simulator ($100k starting cash, fills at latest delayed quote, no real orders anywhere). Configure strategy instances (ticker + strategy + allocation); the engine evaluates them Mon–Fri at 9:00 ET (pre-open) and 16:30 ET (post-close), plus on demand. Each proposed trade is reviewed by a **Claude analyst** that reads the ticker signals, macro dashboard, and dealer positioning, then approves / downsizes / vetoes with a written rationale shown in the order log. |

## Paper trading notes

- **Simulation only.** The paper engine has no brokerage connectivity — orders exist
  only in `backend/data/paper.db`. Delete that file to reset the account to $100k.
- **Claude analyst runs via the `claude` CLI, not the API.** It shells out to the
  `claude` command in headless print mode (`claude -p ... --output-format json
  --json-schema ...`), so review calls draw on your existing Claude subscription
  (Pro/Max) usage allowance instead of separate pay-per-token API billing. Requires
  `claude` on PATH and `claude auth login` run once beforehand (`claude auth status`
  to check). Model defaults to the `sonnet` CLI alias; override with the
  `CLAUDE_ANALYST_MODEL` env var (e.g. `opus`). Without the CLI available, orders
  execute unreviewed and the order log says so — the engine never blocks on it.
  **Known CLI quirk:** `--safe-mode` (used here to skip expensive CLAUDE.md/memory
  auto-discovery) hangs if combined with `--system-prompt` — instructions are sent
  in the user turn instead, not as a system prompt.
- **Scheduler** (APScheduler, 9:00 + 16:30 ET weekdays) only fires while the backend
  process is running; the Paper page shows the last run so gaps are visible.
- Fills use ~15-min delayed quotes; post-close fills approximate the next session's
  open. Long/flat only, no margin, no costs modeled. Paper results will differ from
  live trading.

## Data source & limitations

All data comes from **yfinance** (Yahoo Finance, unauthenticated):

- Quotes are delayed ~15 minutes; options open interest updates overnight.
- Intraday history caps at ~60 days, so VWAP strategies aren't backtestable here.
- Yahoo rate-limits aggressively. Responses are cached in SQLite
  (`backend/data/cache.db`) — daily bars 24 h, intraday/quotes 15 min, chains 1 h.
  If Yahoo is unreachable, the API serves the last cached data and the UI shows a
  "served from cache" banner.
- Backtests use adjusted closes; no commissions, slippage, borrowing, or taxes.

The data layer is isolated in `backend/app/data/client.py` — to upgrade to a paid
provider (Polygon, etc.), reimplement those four functions and everything else works
unchanged.

## Architecture

- `backend/app/data/` — yfinance client + SQLite TTL cache
- `backend/app/analysis/` — pure functions: `indicators.py`, `macro.py`, `gex.py`,
  `relval.py`, `backtest.py`
- `backend/app/routers/` — thin FastAPI routes mapping analysis to `/api/*`
- `frontend/src/pages/` — one page per methodology
- `frontend/src/components/` — lightweight-charts wrappers (candles, lines), stat
  tiles, sparklines

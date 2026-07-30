import { useEffect, useMemo, useState } from 'react'
import { api, type BacktestResult, type StrategySpec } from '../api'
import LineChart from '../components/LineChart'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

const STRATEGY_TIPS: Record<string, string> = {
  rsi_revert: 'Buy when RSI(14) drops below the "buy below" threshold (default 30, oversold), exit when it rises back above the "exit above" threshold (default 50). A mean-reversion strategy — bets that stretched moves snap back.',
  sma_cross: 'Hold long whenever the fast SMA is above the slow SMA (default 50/200 — the golden/death cross), flat otherwise. A trend-following strategy — aims to ride sustained moves and sit out drawn-out downtrends.',
  macd_cross: 'Hold long whenever the MACD line is above its signal line, flat otherwise. A trend/momentum strategy that reacts faster than a slow SMA cross but can whipsaw more in choppy markets.',
  bollinger_revert: 'Buy when price closes below the lower Bollinger Band, exit when it closes back above the middle band. A mean-reversion strategy betting that statistically stretched moves revert toward the average.',
}

const PERIODS = ['2y', '5y', '10y', 'max']

export default function Backtest() {
  const [strategies, setStrategies] = useState<StrategySpec[]>([])
  const [symbol, setSymbol] = useState('AAPL')
  const [strategy, setStrategy] = useState('sma_cross')
  const [period, setPeriod] = useState('5y')
  const [params, setParams] = useState<Record<string, number>>({})
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch((e) => setError((e as Error).message))
  }, [])

  const spec = strategies.find((s) => s.id === strategy)

  useEffect(() => {
    if (spec) setParams(spec.defaultParams)
  }, [spec])

  const run = async () => {
    if (!symbol.trim()) return
    setLoading(true)
    setError(null)
    try {
      setResult(await api.backtest({ symbol: symbol.trim().toUpperCase(), strategy, params, period }))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const lines = useMemo(
    () =>
      result
        ? [
            { points: result.equity, color: '#58a6ff', title: 'Strategy' },
            { points: result.buyHold, color: '#8b949e', title: 'Buy & hold' },
          ]
        : [],
    [result],
  )

  const m = result?.metrics
  const beat = m != null && m.totalReturnPct > m.buyHoldReturnPct

  return (
    <div>
      <h1>Strategy Backtest</h1>
      <div className="controls">
        <input value={symbol} onChange={(e) => setSymbol(e.target.value)} style={{ width: 110 }} placeholder="Ticker" />
        <label style={{ fontSize: 13, color: '#8b949e' }}>
          <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            {strategies.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
          {spec && <Tip text={STRATEGY_TIPS[spec.id] ?? spec.label} />}
        </label>
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          {PERIODS.map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
        {spec && Object.entries(params).map(([k, v]) => (
          <label key={k} style={{ fontSize: 13, color: '#8b949e' }}>
            {k}{' '}
            <input
              type="number"
              value={v}
              onChange={(e) => setParams((p) => ({ ...p, [k]: Number(e.target.value) }))}
              style={{ width: 64 }}
            />
          </label>
        ))}
        <button className="primary" onClick={run} disabled={loading}>
          {loading ? 'Running…' : 'Run backtest'}
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {result?.stale && (
        <div className="stale-banner">Backtest ran on cached data — data source unreachable.</div>
      )}

      {result && m && !error && (
        <>
          <div className="tiles">
            <StatTile label="Strategy return" value={`${m.totalReturnPct}%`} tone={beat ? 'pos' : 'neg'}
              sub={`${result.label} · ${result.period}`}
              tooltip="Total % return of the backtested strategy over the selected period, compounding daily. Signals are computed on today's close but positions change on the next day's close, so the result doesn't rely on foreknowledge of today's close." />
            <StatTile label="Buy & hold" value={`${m.buyHoldReturnPct}%`} sub={result.symbol}
              tooltip="Total % return from simply buying and holding the ticker for the same period — the baseline the strategy is measured against." />
            <StatTile label="CAGR" value={`${m.cagrPct}%`}
              tooltip="Compound Annual Growth Rate — the strategy's return annualized, so results are comparable across backtests of different lengths." />
            <StatTile label="Sharpe" value={m.sharpe}
              tooltip="Annualized average daily return divided by annualized daily volatility (0% risk-free rate assumed). Higher is better; roughly above 1 is considered decent for a standalone strategy. Rewards steady gains, penalizes bumpy ones." />
            <StatTile label="Max drawdown" value={`${m.maxDrawdownPct}%`}
              sub={`buy&hold ${m.buyHoldMaxDrawdownPct}%`} tone="neg"
              tooltip="The largest peak-to-trough decline in the strategy's equity curve. Compared against buy & hold's max drawdown — a strategy can be worth using even without beating total return if it cuts drawdown meaningfully." />
            <StatTile label="Trades" value={m.trades}
              sub={m.winRatePct != null ? `${m.winRatePct}% win rate` : undefined}
              tooltip="Number of completed round-trip trades (entry to exit) over the backtest, with the % that closed profitably." />
            <StatTile label="Time in market" value={`${m.timeInMarketPct}%`}
              tooltip="% of trading days the strategy held a position rather than sitting in cash. Similar returns with lower time in market implies more efficient use of capital and less exposure to risk." />
          </div>
          <div className="panel">
            <h2>
              Equity curve (growth of $1, signals executed next close)
              <Tip text="Blue = strategy equity curve, gray = buy & hold, both starting at $1. Signals are computed on today's close but executed on tomorrow's close to avoid look-ahead bias." />
            </h2>
            <LineChart lines={lines} height={340} />
          </div>
          <p className="muted" style={{ fontSize: 12 }}>
            Long/flat only, no costs/slippage/dividends modeled beyond adjusted closes. Past
            performance does not indicate future results.
          </p>
        </>
      )}
      {!result && !error && (
        <div className="loading">Pick a ticker and strategy, then run a backtest.</div>
      )}
    </div>
  )
}

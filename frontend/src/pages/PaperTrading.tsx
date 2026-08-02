import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  type AutoDeployResult,
  type HealthCheckRow,
  type PaperAccount,
  type PaperInstance,
  type PaperOrder,
  type PaperOverlapEntry,
  type PaperPlan,
  type PaperPortfolio,
  type PaperRun,
  type Point,
  type ScanResult,
  type StrategySpec,
  type SustainedSignal,
} from '../api'
import CongressModal from '../components/CongressModal'
import LineChart from '../components/LineChart'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

const SOURCE_COLORS: Record<string, string> = {
  technical:      '#58a6ff',
  sustained:      '#3fb950',
  smart_buy:      '#d29922',
  smart_universe: '#bc8cff',
  congress:       '#f78166',
}
const SOURCE_LABELS: Record<string, string> = {
  technical:      'Technical',
  sustained:      'Sustained',
  smart_buy:      'Smart Buy',
  smart_universe: 'Smart Universe',
  congress:       'Congress',
}

type SourceFilter = 'all' | 'smartBuy' | 'technicalSustained'
type PositionSortKey = 'symbol' | 'qty' | 'avgCost' | 'last' | 'pctChg' | 'stopLoss' | 'takeProfit' | 'value' | 'unrealizedPnl'
type SortDir = 'asc' | 'desc'

function SortTh<K extends string>(
  { col, label, sort, onSort }: { col: K; label: string; sort: { key: K; dir: SortDir }; onSort: (col: K) => void },
) {
  return (
    <th onClick={() => onSort(col)} style={{ cursor: 'pointer', userSelect: 'none' }}>
      {label}{sort.key === col ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )
}

const SOURCE_FILTER_LABELS: Record<SourceFilter, string> = {
  all: 'All sources',
  smartBuy: 'Smart Buy',
  technicalSustained: 'Technical/Sustained',
}

const PORTFOLIO_ID_FOR_FILTER: Record<Exclude<SourceFilter, 'all'>, string> = {
  smartBuy: 'smart_buy',
  technicalSustained: 'technical_sustained',
}

function bucketOfTags(tags: string[] | null | undefined): 'smartBuy' | 'technicalSustained' {
  return tags && tags.includes('smart_buy') ? 'smartBuy' : 'technicalSustained'
}

// Merges multiple portfolios' equity curves into one combined series by
// carrying each portfolio's last-known value forward and summing at every
// timestamp any portfolio snapshotted at (engine runs mark all portfolios
// to market together, so most timestamps line up across portfolios).
function combineCurves(curves: Point[][]): Point[] {
  const allTimes = [...new Set(curves.flatMap((c) => c.map((p) => p.time)))].sort((a, b) => a - b)
  return allTimes.map((time) => {
    let sum = 0
    for (const c of curves) {
      let val: number | undefined
      for (const p of c) {
        if (p.time > time) break
        val = p.value
      }
      sum += val ?? 0
    }
    return { time, value: sum }
  })
}

function SourceBadges({ tags, onPoliticianClick }: { tags: string[]; onPoliticianClick?: () => void }) {
  if (!tags || tags.length === 0) return null
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, marginTop: 2 }}>
      {tags.map((t) => {
        const clickable = (t === 'congress' || t === 'smart_buy') && !!onPoliticianClick
        return (
          <span
            key={t}
            onClick={clickable ? (e) => { e.stopPropagation(); onPoliticianClick!() } : undefined}
            title={clickable ? 'Click to see politician details' : undefined}
            style={{
              fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 600,
              background: (SOURCE_COLORS[t] ?? '#8b949e') + '22',
              color: SOURCE_COLORS[t] ?? '#8b949e',
              border: `1px solid ${(SOURCE_COLORS[t] ?? '#8b949e')}55`,
              whiteSpace: 'nowrap',
              cursor: clickable ? 'pointer' : undefined,
              textDecoration: clickable ? 'underline dotted' : undefined,
            }}
          >
            {SOURCE_LABELS[t] ?? t}
          </span>
        )
      })}
    </div>
  )
}

function fmtTime(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtUsd(v: number): string {
  return v.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

function NameSub({ sym, names }: { sym: string; names: Record<string, string> }) {
  const name = names[sym]
  if (!name || name === sym) return null
  return <div style={{ fontSize: 11, color: '#8b949e', marginTop: 1, fontWeight: 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>{name}</div>
}

export default function PaperTrading() {
  const [account, setAccount] = useState<PaperAccount | null>(null)
  const [portfolioEquity, setPortfolioEquity] = useState<Record<string, Point[]>>({})
  const [legacyEquity, setLegacyEquity] = useState<Point[]>([])
  const [overlaps, setOverlaps] = useState<PaperOverlapEntry[]>([])
  const [orders, setOrders] = useState<PaperOrder[]>([])
  const [runs, setRuns] = useState<PaperRun[]>([])
  const [instances, setInstances] = useState<PaperInstance[]>([])
  const [openPlans, setOpenPlans] = useState<PaperPlan[]>([])
  const [strategies, setStrategies] = useState<StrategySpec[]>([])
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [expandedOrder, setExpandedOrder] = useState<number | null>(null)
  const [lastRunResult, setLastRunResult] = useState<Awaited<ReturnType<typeof api.paperRun>> | null>(null)

  const [newSymbol, setNewSymbol] = useState('')
  const [newStrategy, setNewStrategy] = useState('sma_cross')
  const [newAllocation, setNewAllocation] = useState(10000)
  const [newPortfolioId, setNewPortfolioId] = useState<string>('technical_sustained')

  const [healthRunning, setHealthRunning] = useState(false)
  const [healthResults, setHealthResults] = useState<HealthCheckRow[] | null>(null)
  const [resetConfirm, setResetConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [names, setNames] = useState<Record<string, string>>({})
  const [congressModal, setCongressModal] = useState<string | null>(null)
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [posSort, setPosSort] = useState<{ key: PositionSortKey; dir: SortDir }>({ key: 'symbol', dir: 'asc' })

  const [autoDeploying, setAutoDeploying] = useState(false)
  const [autoDeployResult, setAutoDeployResult] = useState<AutoDeployResult | null>(null)
  const [showManual, setShowManual] = useState(false)
  const [suggestions, setSuggestions] = useState<SustainedSignal[] | null>(null)
  const [suggestionKind, setSuggestionKind] = useState<'sustained' | 'quickscan' | null>(null)
  const [loadingSuggestions, setLoadingSuggestions] = useState(false)
  const [deployingSymbol, setDeployingSymbol] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [acc, eq, ovl, ords, rns, inst, plans] = await Promise.all([
        api.paperAccount(),
        api.paperEquity(),
        api.paperOverlap(),
        api.paperOrders(),
        api.paperRuns(),
        api.paperInstances(),
        api.paperPlans('open', 200),
      ])
      setAccount(acc)
      // dedupe to one point per second — lightweight-charts requires strictly
      // ascending unique timestamps, and fills can snapshot within the same second
      const nextPortfolioEquity: Record<string, Point[]> = {}
      for (const pid of Object.keys(eq.portfolios)) {
        const bySecond = new Map<number, number>()
        for (const p of eq.portfolios[pid].curve) bySecond.set(Math.floor(p.ts), p.equity)
        nextPortfolioEquity[pid] = [...bySecond.entries()]
          .map(([time, value]) => ({ time, value }))
          .sort((a, b) => a.time - b.time)
      }
      setPortfolioEquity(nextPortfolioEquity)
      const legacyBySecond = new Map<number, number>()
      for (const p of eq.legacy.curve) legacyBySecond.set(Math.floor(p.ts), p.equity)
      setLegacyEquity([...legacyBySecond.entries()].map(([time, value]) => ({ time, value })).sort((a, b) => a.time - b.time))
      setOverlaps(ovl.overlaps)
      setOrders(ords.orders)
      setRuns(rns.runs)
      setInstances(inst.instances)
      setOpenPlans(plans.plans)
      setError(null)
      const allSyms = [...new Set([
        ...acc.combined.positions.map((p) => p.symbol),
        ...inst.instances.map((i) => i.symbol),
        ...ords.orders.map((o) => o.symbol),
      ])]
      if (allSyms.length > 0) {
        api.tickerNames(allSyms).then((r) => setNames((n) => ({ ...n, ...r.names }))).catch(() => {})
      }
    } catch (e) {
      setError((e as Error).message)
    }
  }, [])

  useEffect(() => {
    api.strategies().then((r) => setStrategies(r.strategies)).catch(() => {})
    refresh()
  }, [refresh])

  const runNow = async () => {
    setRunning(true)
    setError(null)
    setLastRunResult(null)
    try {
      const res = await api.paperRun()
      setLastRunResult(res)
      if (res.errors.length) setError(`Run errors: ${res.errors.join('; ')}`)
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const addInstance = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newSymbol.trim()) return
    setError(null)
    try {
      await api.paperCreateInstance({
        symbol: newSymbol.trim().toUpperCase(),
        strategy: newStrategy,
        allocationUsd: newAllocation,
        portfolioId: newPortfolioId,
      })
      setNewSymbol('')
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const handleAutoDeploy = async (symbol?: string) => {
    const sym = (symbol ?? newSymbol).trim().toUpperCase()
    if (!sym) return
    if (symbol) {
      setDeployingSymbol(sym)
    } else {
      setAutoDeploying(true)
      setAutoDeployResult(null)
    }
    setError(null)
    try {
      const res = await api.autoDeploy(sym, newAllocation, newPortfolioId)
      setAutoDeployResult(res)
      if (!symbol) setNewSymbol('')
      await refresh()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAutoDeploying(false)
      setDeployingSymbol(null)
    }
  }

  const QUICK_SCAN_TICKERS = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'JPM', 'V', 'SPY', 'QQQ', 'XLK', 'XLF', 'XLE', 'GLD']

  const loadSuggestions = async () => {
    setLoadingSuggestions(true)
    try {
      const res = await api.sustainedSignals(2, 3)
      if (res.results.length > 0) {
        setSuggestions(res.results)
        setSuggestionKind('sustained')
      } else {
        // No watchlist history — fall back to a one-shot scan of popular tickers
        const scan = await api.scanTickers(QUICK_SCAN_TICKERS)
        const mapped: SustainedSignal[] = (scan.results as ScanResult[])
          .filter((r) => !r.error && r.score != null)
          .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
          .map((r) => ({
            symbol: r.symbol,
            streak: 1,
            score: r.score,
            trend: r.trend,
            rsi: r.rsi ?? null,
            macdAboveSignal: r.macdAboveSignal ?? null,
            recentCross: r.recentCross ?? null,
            atrPct: r.atrPct ?? null,
            lastPrice: r.last ?? null,
            stale: r.stale,
            history: [],
          }))
        setSuggestions(mapped)
        setSuggestionKind('quickscan')
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoadingSuggestions(false)
    }
  }

  const activePortfolio: PaperPortfolio | null = account
    ? (sourceFilter === 'all' ? account.combined : account.portfolios[PORTFOLIO_ID_FOR_FILTER[sourceFilter]])
    : null

  const equity = useMemo(() => {
    if (sourceFilter === 'all') {
      const combined = combineCurves(Object.values(portfolioEquity))
      // The per-portfolio curves only start from the day portfolios were split out
      // of the old pooled account — splice the preserved pre-split pooled history
      // (equity_snapshots_legacy) onto the front so "All sources" still shows the
      // full history instead of just the last few hours.
      const cutoff = combined.length > 0 ? combined[0].time : Infinity
      return [...legacyEquity.filter((p) => p.time < cutoff), ...combined]
    }
    return portfolioEquity[PORTFOLIO_ID_FOR_FILTER[sourceFilter]] ?? []
  }, [portfolioEquity, legacyEquity, sourceFilter])

  const equityLines = useMemo(
    () => (equity.length > 1 ? [{ points: equity, color: '#58a6ff', title: 'Equity' }] : []),
    [equity],
  )

  const lastRun = runs[0]
  const pnlTone = activePortfolio && activePortfolio.totalPnl >= 0 ? 'pos' : 'neg'

  const enrichedPositions = (activePortfolio?.positions ?? []).map((p) => {
    const plan = openPlans.find((pl) => pl.symbol === p.symbol)
    const pctChg = p.last != null && p.avgCost ? (p.last / p.avgCost - 1) * 100 : null
    return { ...p, plan, pctChg }
  })
  const displayedPositions = enrichedPositions.slice().sort((a, b) => {
    const { key, dir } = posSort
    const mul = dir === 'asc' ? 1 : -1
    if (key === 'symbol') return mul * a.symbol.localeCompare(b.symbol)
    const raw = (row: typeof a) =>
      key === 'stopLoss' ? row.plan?.stop_loss
      : key === 'takeProfit' ? row.plan?.take_profit
      : row[key]
    const va = raw(a), vb = raw(b)
    const na = va == null ? -Infinity : Number(va)
    const nb = vb == null ? -Infinity : Number(vb)
    return mul * (na - nb)
  })
  const displayedInstances = instances.filter((i) =>
    sourceFilter === 'all' || bucketOfTags(i.source_tags) === sourceFilter,
  )
  const togglePosSort = (key: PositionSortKey) =>
    setPosSort((s) => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })

  return (
    <div>
      <h1>Paper Trading</h1>
      <div className="stale-banner" style={{ borderColor: '#58a6ff', color: '#58a6ff', background: 'rgba(88,166,255,0.08)' }}>
        Simulated paper trading — no real orders are placed anywhere. Fills use ~15-min delayed quotes.
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="controls">
        <button className="primary" onClick={runNow} disabled={running}>
          {running ? 'Running engine…' : 'Run engine now'}
        </button>
        <Tip text="Evaluates every enabled strategy instance against the latest daily bars, sends any proposed trades through the Claude analyst for review, and executes approved orders on the simulated account. The engine also runs automatically Mon–Fri at 9:00 AM (pre-open) and 4:30 PM (post-close) ET while the backend is running." />
        <button onClick={async () => {
          setHealthRunning(true)
          try { const r = await api.healthCheck(); setHealthResults(r.results) }
          catch (e) { setError((e as Error).message) }
          finally { setHealthRunning(false) }
        }} disabled={healthRunning}>
          {healthRunning ? 'Checking…' : 'Health check'}
        </button>
        <Tip text="Re-runs a 2y backtest on every enabled instance's current strategy. Flags any whose Sharpe has dropped below 0.5 (watch) or 0.2 (disable). Use this periodically to catch strategies that have stopped working on the tickers you're trading." />
        {lastRun && (
          <span className="muted" style={{ fontSize: 13 }}>
            Last run: {lastRun.kind} at {fmtTime(lastRun.started_at)} — {lastRun.filled} filled,{' '}
            {lastRun.vetoed} vetoed{lastRun.errors.length > 0 ? `, ${lastRun.errors.length} errors` : ''}
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {!resetConfirm ? (
            <button style={{ fontSize: 12, padding: '3px 10px', color: '#f85149', borderColor: '#f8514955' }}
              onClick={() => setResetConfirm(true)}>
              {sourceFilter === 'all' ? 'Reset account' : `Reset ${SOURCE_FILTER_LABELS[sourceFilter]}`}
            </button>
          ) : (
            <>
              <span style={{ fontSize: 12, color: '#f85149' }}>
                {sourceFilter === 'all'
                  ? 'Wipe ALL portfolios and restore starting cash?'
                  : `Wipe the ${SOURCE_FILTER_LABELS[sourceFilter]} portfolio and restore its starting cash?`}
              </span>
              <button style={{ fontSize: 12, padding: '3px 10px', background: '#f85149', color: '#fff', borderColor: '#f85149' }}
                disabled={resetting}
                onClick={async () => {
                  setResetting(true)
                  try {
                    if (sourceFilter === 'all') {
                      await api.paperResetAll()
                    } else {
                      await api.paperReset(PORTFOLIO_ID_FOR_FILTER[sourceFilter])
                    }
                    setResetConfirm(false)
                    setLastRunResult(null)
                    setHealthResults(null)
                    await refresh()
                  } catch (e) {
                    setError((e as Error).message)
                  } finally {
                    setResetting(false)
                  }
                }}>
                {resetting ? 'Resetting…' : 'Confirm reset'}
              </button>
              <button style={{ fontSize: 12, padding: '3px 10px' }}
                onClick={() => setResetConfirm(false)}>
                Cancel
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── run result breakdown ─────────────────────────────────────────── */}
      {lastRunResult && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2 style={{ marginBottom: 10 }}>Last run result</h2>

          {lastRunResult.results.length === 0 && lastRunResult.exits.length === 0 && (
            <p className="muted" style={{ fontSize: 13 }}>
              No enabled instances to evaluate. Add a strategy instance below and enable it.
            </p>
          )}

          {lastRunResult.results.length > 0 && (
            <table className="data" style={{ marginBottom: lastRunResult.exits.length > 0 ? 12 : 0 }}>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Outcome</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {lastRunResult.results.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <strong>{r.symbol}</strong>
                      <NameSub sym={r.symbol} names={names} />
                    </td>
                    <td>
                      {r.action === 'filled' && (
                        <span style={{ color: '#3fb950', fontWeight: 600 }}>
                          {r.direction === 'long' ? 'Bought' : r.direction === 'short' ? 'Shorted' : 'Filled'}
                        </span>
                      )}
                      {r.action === 'vetoed' && (
                        <span style={{ color: '#f85149', fontWeight: 600 }}>Vetoed</span>
                      )}
                      {r.action === 'hold' && (
                        <span className="muted">Hold</span>
                      )}
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {r.action === 'hold' && (
                        `Strategy not signaling — desired=${r.desired ?? 0}, held=${r.held ?? 0}`
                      )}
                      {r.action === 'filled' && r.fillPrice != null && (
                        `Fill @ $${r.fillPrice}`
                      )}
                      {r.action === 'vetoed' && (
                        `Order #${r.orderId} — see Orders tab for analyst rationale`
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {lastRunResult.exits.length > 0 && (
            <>
              <h3 style={{ fontSize: 13, marginBottom: 6 }}>Exits triggered</h3>
              <table className="data">
                <thead><tr><th>Symbol</th><th>Reason</th><th>P&L</th></tr></thead>
                <tbody>
                  {lastRunResult.exits.map((e, i) => (
                    <tr key={i}>
                      <td><strong>{e.symbol}</strong></td>
                      <td className="muted" style={{ fontSize: 12 }}>{e.reason?.replace('_', ' ')}</td>
                      <td className={e.pnl != null ? (e.pnl >= 0 ? 'pos' : 'neg') : ''}>
                        {e.pnl != null ? `$${e.pnl.toFixed(2)}` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}

      {account && activePortfolio && (
        <div className="tiles">
          <StatTile label="Equity" value={fmtUsd(activePortfolio.equity)}
            tooltip={sourceFilter === 'all'
              ? "Combined value across all portfolios: cash plus all open positions marked at the latest quotes."
              : `Total value of the ${SOURCE_FILTER_LABELS[sourceFilter]} portfolio: its own cash plus its own open positions.`} />
          <StatTile label="Cash" value={fmtUsd(activePortfolio.cash)}
            tooltip="Uninvested simulated cash. Buys are rejected if they exceed available cash — no margin or leverage in this simulator." />
          <StatTile label="Total P&L" value={`${fmtUsd(activePortfolio.totalPnl)} (${activePortfolio.totalPnlPct != null ? activePortfolio.totalPnlPct : '—'}%)`} tone={pnlTone}
            tooltip="Equity minus starting equity — combined realized and unrealized profit/loss since this portfolio began." />
          <StatTile label="Open positions" value={activePortfolio.positions.length}
            tooltip="Number of tickers currently held in this view. Each strategy instance holds at most one long position in its ticker (long/flat, no shorting)." />
          <StatTile
            label="Next scheduled run"
            value={account.schedulerJobs.length > 0
              ? new Date(Math.min(...account.schedulerJobs.map((j) => j.nextRun ? Date.parse(j.nextRun) : Infinity))).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })
              : 'scheduler off'}
            sub="9:00 & 16:30 ET weekdays"
            tooltip="The engine runs twice each trading day: before the open (9:00 ET, re-checks and marks the portfolio to market) and after the close (16:30 ET, evaluates the completed daily bar — the run that generates most trades). Runs only happen while the backend server is running."
          />
        </div>
      )}

      {equityLines.length > 0 && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Equity curve
            <Tip text="Account value over time, snapshotted after every engine run and order fill. Compare its shape against the Backtest page to see whether live paper results track the backtested expectation." />
          </h2>
          <LineChart lines={equityLines} height={240} />
        </div>
      )}

      {healthResults && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Health Check Results
            <Tip text="Sharpe ratio from a fresh 2y backtest on each instance's current strategy. Keep: Sharpe ≥ 0.5. Watch: 0.2–0.5. Disable: < 0.2 — the strategy has not historically worked well enough on this ticker to justify live paper risk." />
            <button style={{ marginLeft: 12, fontSize: 12 }} onClick={() => setHealthResults(null)}>Dismiss</button>
          </h2>
          <table className="data">
            <thead>
              <tr><th>Symbol</th><th>Strategy</th><th>Sharpe (2y)</th><th>Total return</th><th>Max DD</th><th>Status</th><th></th></tr>
            </thead>
            <tbody>
              {healthResults.map((r) => (
                <tr key={r.instanceId}>
                  <td>
                    <strong>{r.symbol}</strong>
                    <NameSub sym={r.symbol} names={names} />
                  </td>
                  <td style={{ fontSize: 12 }}>{r.strategy}</td>
                  <td className={r.sharpe == null ? '' : r.sharpe >= 0.5 ? 'pos' : r.sharpe >= 0.2 ? 'warn' : 'neg'}>
                    {r.sharpe?.toFixed(2) ?? (r.error ? 'err' : '—')}
                  </td>
                  <td className={r.totalReturnPct != null && r.totalReturnPct >= 0 ? 'pos' : 'neg'}>
                    {r.totalReturnPct != null ? `${r.totalReturnPct > 0 ? '+' : ''}${r.totalReturnPct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="neg">{r.maxDrawdownPct != null ? `${r.maxDrawdownPct.toFixed(1)}%` : '—'}</td>
                  <td>
                    <span style={{ color: r.recommendation === 'keep' ? '#3fb950' : r.recommendation === 'watch' ? '#d29922' : '#f85149', fontSize: 12, fontWeight: 600 }}>
                      {r.recommendation.toUpperCase()}
                    </span>
                  </td>
                  <td>
                    {r.recommendation !== 'keep' && r.enabled && (
                      <button style={{ fontSize: 12, padding: '2px 8px' }}
                        onClick={() => api.paperUpdateInstance(r.instanceId, { enabled: false }).then(refresh)}>
                        Disable
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="controls" style={{ marginBottom: 10 }}>
        {(['all', 'smartBuy', 'technicalSustained'] as SourceFilter[]).map((f) => (
          <button key={f} className={sourceFilter === f ? 'primary' : ''}
            onClick={() => {
              setSourceFilter(f)
              if (f !== 'all') setNewPortfolioId(PORTFOLIO_ID_FOR_FILTER[f])
            }}>
            {SOURCE_FILTER_LABELS[f]}
          </button>
        ))}
      </div>

      <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Positions
            <Tip text="Open holdings marked to the latest (delayed) quote. Unrealized P&L compares current value against average cost." />
          </h2>
          {account && displayedPositions.length > 0 ? (
            <table className="data">
              <thead>
                <tr>
                  <SortTh col="symbol" label="Symbol" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="qty" label="Qty" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="avgCost" label="Avg cost" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="last" label="Last" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="pctChg" label="% Chg" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="stopLoss" label="Stop" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="takeProfit" label="Target" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="value" label="Value" sort={posSort} onSort={togglePosSort} />
                  <SortTh col="unrealizedPnl" label="Unrealized" sort={posSort} onSort={togglePosSort} />
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {displayedPositions.map((p) => (
                  <tr key={p.symbol}>
                    <td>
                      {p.symbol}
                      <NameSub sym={p.symbol} names={names} />
                    </td>
                    <td>{p.qty}</td>
                    <td>{p.avgCost.toFixed(2)}</td>
                    <td>{p.last?.toFixed(2) ?? '—'}</td>
                    <td className={p.pctChg == null ? '' : p.pctChg >= 0 ? 'pos' : 'neg'}>
                      {p.pctChg == null ? '—' : `${p.pctChg > 0 ? '+' : ''}${p.pctChg.toFixed(2)}%`}
                    </td>
                    <td className="neg">{p.plan?.stop_loss.toFixed(2) ?? '—'}</td>
                    <td className="pos">{p.plan?.take_profit.toFixed(2) ?? '—'}</td>
                    <td>{fmtUsd(p.value)}</td>
                    <td className={p.unrealizedPnl >= 0 ? 'pos' : 'neg'}>{fmtUsd(p.unrealizedPnl)}</td>
                    <td>
                      <SourceBadges
                        tags={p.plan?.source_tags ?? []}
                        onPoliticianClick={() => setCongressModal(p.symbol)}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">
              {sourceFilter === 'all' ? 'No open positions.' : `No ${SOURCE_FILTER_LABELS[sourceFilter].toLowerCase()} positions.`}
            </p>
          )}
      </div>

      {overlaps.length > 0 && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Overlap
            <Tip text="Symbols held independently by more than one portfolio at the same time. Portfolios don't block each other from trading the same ticker — this table shows where their positions diverge (entry price, direction, sizing) so you can see how the strategies actually differ." />
          </h2>
          <table className="data">
            <thead>
              <tr><th>Symbol</th><th>Portfolio</th><th>Direction</th><th>Qty</th><th>Entry</th><th>Stop</th><th>Target</th><th>Opened</th></tr>
            </thead>
            <tbody>
              {overlaps.map((o) => o.portfolios.map((p, i) => (
                <tr key={`${o.symbol}-${p.portfolioId}`}>
                  {i === 0 && (
                    <td rowSpan={o.portfolios.length}>
                      <strong>{o.symbol}</strong>
                      <NameSub sym={o.symbol} names={names} />
                    </td>
                  )}
                  <td>{account?.portfolios[p.portfolioId]?.label ?? p.portfolioId}</td>
                  <td className={p.direction === 'long' ? 'pos' : 'neg'}>{p.direction.toUpperCase()}</td>
                  <td>{p.qty}</td>
                  <td>{p.entryPrice.toFixed(2)}</td>
                  <td className="neg">{p.stopLoss.toFixed(2)}</td>
                  <td className="pos">{p.takeProfit.toFixed(2)}</td>
                  <td>{fmtTime(p.openedAt)}</td>
                </tr>
              )))}
            </tbody>
          </table>
        </div>
      )}

      <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Strategy instances
            <Tip text="Each instance pairs one ticker with one strategy and a capital allocation. On every engine run the strategy's signal is recomputed from daily bars: signal on + no position = propose a buy; signal off + holding = propose a sell. Disable an instance to pause it without deleting its history." />
          </h2>
          {/* ── add form ─────────────────────────────────────── */}
          <div style={{ marginBottom: 12 }}>
            <div className="controls" style={{ marginBottom: 6 }}>
              <input value={newSymbol} onChange={(e) => setNewSymbol(e.target.value)}
                placeholder="Ticker" style={{ width: 90 }}
                onKeyDown={(e) => e.key === 'Enter' && handleAutoDeploy()} />
              <input type="number" value={newAllocation} min={100} step={500}
                onChange={(e) => setNewAllocation(Number(e.target.value))} style={{ width: 100 }} />
              <select value={newPortfolioId} onChange={(e) => setNewPortfolioId(e.target.value)}>
                {account && Object.keys(account.portfolios).length > 0
                  ? Object.entries(account.portfolios).map(([pid, p]) => (
                      <option key={pid} value={pid}>{p.label ?? pid}</option>
                    ))
                  : (['smart_buy', 'technical_sustained'] as const).map((pid) => (
                      <option key={pid} value={pid}>{SOURCE_FILTER_LABELS[pid === 'smart_buy' ? 'smartBuy' : 'technicalSustained']}</option>
                    ))}
              </select>
              <Tip text="Which portfolio's own cash this new instance draws from. Portfolios are fully independent — capital, positions, and P&L never cross over between them." />
              <button className="primary" onClick={() => handleAutoDeploy()}
                disabled={autoDeploying || !newSymbol.trim()}>
                {autoDeploying ? 'Analyzing…' : 'Auto-deploy'}
              </button>
              <Tip text="Backtests every available strategy on this ticker over the past 2 years and deploys the one with the best risk-adjusted return (Sharpe ratio). No strategy knowledge needed." />
              <button type="button" onClick={() => setShowManual(!showManual)}
                style={{ fontSize: 12, padding: '3px 8px' }}>
                {showManual ? '▴ Hide' : '▾ Manual'}
              </button>
            </div>

            {showManual && (
              <form onSubmit={addInstance} className="controls" style={{ marginBottom: 6 }}>
                <select value={newStrategy} onChange={(e) => setNewStrategy(e.target.value)}>
                  {strategies.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                </select>
                <button className="primary" type="submit">Add with this strategy</button>
              </form>
            )}

            {autoDeployResult && (
              <div style={{ fontSize: 12, padding: '6px 10px', background: 'rgba(63,185,80,0.1)', borderRadius: 4, marginBottom: 6 }}>
                <strong>{autoDeployResult.symbol}</strong> deployed with{' '}
                <strong>{autoDeployResult.strategyLabel}</strong>
                {autoDeployResult.sharpe != null && (
                  <span className="muted"> · Sharpe {autoDeployResult.sharpe.toFixed(2)}</span>
                )}
                {autoDeployResult.totalReturnPct != null && (
                  <span className="muted"> · 2y return {autoDeployResult.totalReturnPct > 0 ? '+' : ''}{autoDeployResult.totalReturnPct.toFixed(1)}%</span>
                )}
                <button onClick={() => setAutoDeployResult(null)}
                  style={{ marginLeft: 10, fontSize: 11, padding: '1px 6px' }}>✕</button>
              </div>
            )}

            <div className="controls" style={{ marginTop: 2 }}>
              <button onClick={loadSuggestions} disabled={loadingSuggestions} style={{ fontSize: 12, padding: '3px 10px' }}>
                {loadingSuggestions ? 'Loading…' : suggestions !== null ? '↺ Refresh suggestions' : 'Suggest from screener'}
              </button>
              <Tip text="Shows tickers from your screener watchlist that have had a sustained high score across 3+ consecutive scans — a more reliable setup signal. Click Deploy to auto-select the best backtested strategy." />
            </div>

            {suggestions !== null && suggestions.length === 0 && (
              <p className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                No signals found. Try again later.
              </p>
            )}

            {suggestions !== null && suggestions.length > 0 && (
              <>
                <p className="muted" style={{ fontSize: 11, marginTop: 6, marginBottom: 4 }}>
                  {suggestionKind === 'sustained'
                    ? `Tickers from your screener watchlist with sustained high scores across 3+ scans — stronger setups.`
                    : `Quick scan of popular tickers ranked by signal score. Add tickers to your Screener watchlist for sustained-signal tracking.`}
                </p>
              <table className="data" style={{ marginTop: 4, fontSize: 12 }}>
                <thead>
                  <tr><th>Symbol</th><th>Score</th>{suggestionKind === 'sustained' && <th>Streak</th>}<th>Trend</th><th>RSI</th><th>Price</th><th></th></tr>
                </thead>
                <tbody>
                  {suggestions.map((s) => (
                    <tr key={s.symbol}>
                      <td><strong>{s.symbol}</strong></td>
                      <td className={s.score >= 3 ? 'pos' : s.score <= -2 ? 'neg' : ''}>
                        {s.score > 0 ? '+' : ''}{s.score}
                      </td>
                      {suggestionKind === 'sustained' && <td className="muted">{s.streak}×</td>}
                      <td className="muted">{s.trend ?? '—'}</td>
                      <td className="muted">{s.rsi?.toFixed(0) ?? '—'}</td>
                      <td className="muted">{s.lastPrice != null ? `$${s.lastPrice.toFixed(2)}` : '—'}</td>
                      <td>
                        <button style={{ fontSize: 11, padding: '2px 8px' }}
                          disabled={deployingSymbol === s.symbol}
                          onClick={() => handleAutoDeploy(s.symbol)}>
                          {deployingSymbol === s.symbol ? '…' : 'Deploy'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </>
            )}
          </div>

          {displayedInstances.length > 0 ? (
            <table className="data">
              <thead>
                <tr><th>Symbol</th><th>Strategy</th><th>Allocation</th><th>Source</th><th>Enabled</th><th></th></tr>
              </thead>
              <tbody>
                {displayedInstances.map((i) => (
                  <tr key={i.id} style={i.enabled ? undefined : { opacity: 0.5 }}>
                    <td>
                      {i.symbol}
                      <NameSub sym={i.symbol} names={names} />
                    </td>
                    <td>{strategies.find((s) => s.id === i.strategy)?.label ?? i.strategy}</td>
                    <td>{fmtUsd(i.allocation_usd)}</td>
                    <td>
                      <SourceBadges
                        tags={i.source_tags ?? []}
                        onPoliticianClick={() => setCongressModal(i.symbol)}
                      />
                    </td>
                    <td>
                      <input type="checkbox" checked={i.enabled}
                        onChange={() => api.paperUpdateInstance(i.id, { enabled: !i.enabled }).then(refresh)} />
                    </td>
                    <td>
                      <button onClick={() => api.paperDeleteInstance(i.id).then(refresh)}
                        style={{ fontSize: 12, padding: '3px 8px' }}>✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">
              {sourceFilter === 'all' || instances.length === 0
                ? 'No instances yet — add a ticker and strategy above.'
                : `No ${SOURCE_FILTER_LABELS[sourceFilter].toLowerCase()} instances.`}
            </p>
          )}
      </div>

      <div className="panel">
        <h2>
          Orders &amp; analyst decisions
          <Tip text="Every trade the engine proposed. Before execution, each order is reviewed by a Claude analyst that reads the ticker's signals, the macro dashboard, and dealer positioning — it can approve, shrink, or veto the trade, and its written reasoning appears here. Click a row to read the full rationale. If no Anthropic API key is configured, orders execute unreviewed and are labeled as such." />
        </h2>
        {orders.length > 0 ? (
          <table className="data">
            <thead>
              <tr><th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th><th>Fill</th><th>Analyst</th></tr>
            </thead>
            <tbody>
              {orders.map((o) => (
                <Fragment key={o.id}>
                  <tr onClick={() => setExpandedOrder(expandedOrder === o.id ? null : o.id)}
                    style={{ cursor: 'pointer', textDecoration: o.status === 'vetoed' ? 'line-through' : undefined,
                             opacity: o.status === 'vetoed' ? 0.65 : 1 }}>
                    <td>{fmtTime(o.proposed_at)}</td>
                    <td>
                      {o.symbol}
                      <NameSub sym={o.symbol} names={names} />
                    </td>
                    <td className={o.side === 'buy' ? 'pos' : 'neg'}>{o.side.toUpperCase()}</td>
                    <td>{o.qty}</td>
                    <td className={o.status === 'filled' ? 'pos' : o.status === 'vetoed' ? 'warn' : o.status === 'error' ? 'neg' : ''}>
                      {o.status}
                    </td>
                    <td>{o.fill_price ? o.fill_price.toFixed(2) : '—'}</td>
                    <td className="muted" style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {o.verdict ? `${o.verdict}${o.size_factor != null && o.size_factor < 1 ? ` @ ${Math.round(o.size_factor * 100)}%` : ''}` : '—'}
                    </td>
                  </tr>
                  {expandedOrder === o.id && (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'left', background: '#0d1117', fontSize: 13 }}>
                        <div style={{ padding: '6px 4px' }}>
                          <span className="muted">{o.note ?? 'signal'} · run: {o.run_kind}
                            {o.model ? ` · reviewed by ${o.model}` : ''}</span>
                          <div style={{ marginTop: 6 }}>{o.rationale ?? 'No analyst rationale recorded.'}</div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted">No orders yet — add an instance and run the engine.</p>
        )}
      </div>

      {congressModal && (
        <CongressModal symbol={congressModal} onClose={() => setCongressModal(null)} />
      )}
    </div>
  )
}

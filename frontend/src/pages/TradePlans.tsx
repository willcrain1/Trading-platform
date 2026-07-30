import { Fragment, useCallback, useEffect, useState } from 'react'
import { api, type AutoSelectRunLogEntry, type PaperPlan, type PaperStats } from '../api'
import CongressModal from '../components/CongressModal'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

type View = 'trades' | 'autoLog'
type Filter = 'all' | 'open' | 'closed'
type SourceFilter = 'all' | 'smartBuy' | 'technicalSustained'

const SOURCE_FILTER_LABELS: Record<SourceFilter, string> = {
  all: 'All sources',
  smartBuy: 'Smart Buy',
  technicalSustained: 'Technical/Sustained',
}

function bucketOfTags(tags: string[] | null | undefined): 'smartBuy' | 'technicalSustained' {
  return tags && tags.includes('smart_buy') ? 'smartBuy' : 'technicalSustained'
}

function fmtTime(ts: number | null): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: '2-digit' })
}

function fmtUsd(v: number): string {
  return v.toLocaleString(undefined, { style: 'currency', currency: 'USD' })
}

function fmtDateTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

const RUN_KIND_LABELS: Record<string, string> = {
  open: '10:05 AM run',
  close: '4:05 PM run',
  manual: 'Manual run',
  auto: 'Auto run',
}

function runSummary(r: AutoSelectRunLogEntry): string {
  if (r.skipped === 'low_cash') {
    return `Skipped — cash below the ${(r.cash != null && r.equity ? (r.cash / r.equity * 100).toFixed(1) : '—')}% floor`
  }
  if (r.skipped) return `Skipped — ${r.skipped}`
  if (r.candidates === 0 && r.selected === 0 && r.eligible === 0 && !r.errors.length) {
    return 'No candidates gathered this cycle'
  }
  if (r.eligible === 0) return `${r.candidates} candidate(s) found, none eligible (already held or blocked)`
  if (r.selected === 0) return `${r.candidates} candidates → ${r.eligible} eligible, none sized/created`
  return `${r.candidates} candidates → ${r.eligible} eligible → ${r.selected} selected`
}

function holdDays(plan: PaperPlan): string {
  const end = plan.closed_at ?? Date.now() / 1000
  return ((end - plan.opened_at) / 86400).toFixed(0) + 'd'
}

const EXIT_LABELS: Record<string, string> = {
  stop_loss: 'Stop',
  take_profit: 'Target',
  time_stop: 'Time',
  signal_exit: 'Signal',
  manual: 'Manual',
}

const SOURCE_COLORS: Record<string, string> = {
  technical:     '#58a6ff',
  sustained:     '#3fb950',
  smart_buy:     '#d29922',
  smart_universe:'#bc8cff',
  congress:      '#f78166',
}
const SOURCE_LABELS: Record<string, string> = {
  technical:      'Technical',
  sustained:      'Sustained',
  smart_buy:      'Smart Buy',
  smart_universe: 'Smart Universe',
  congress:       'Congress',
}

function SourceBadges({ tags, onPoliticianClick }: { tags: string[]; onPoliticianClick?: () => void }) {
  if (!tags || tags.length === 0) return <span className="muted" style={{ fontSize: 11 }}>—</span>
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
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

export default function TradePlans() {
  const [view, setView] = useState<View>('trades')
  const [filter, setFilter] = useState<Filter>('all')
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>('all')
  const [plans, setPlans] = useState<PaperPlan[]>([])
  const [stats, setStats] = useState<PaperStats | null>(null)
  const [names, setNames] = useState<Record<string, string>>({})
  const [congressModal, setCongressModal] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const [autoLog, setAutoLog] = useState<AutoSelectRunLogEntry[]>([])
  const [autoLogExpanded, setAutoLogExpanded] = useState<number | null>(null)
  const [autoLogError, setAutoLogError] = useState<string | null>(null)
  const [autoLogLoading, setAutoLogLoading] = useState(false)

  const load = useCallback(async (f: Filter) => {
    setLoading(true)
    setError(null)
    try {
      const [p, s] = await Promise.all([api.paperPlans(f), api.paperStats()])
      setPlans(p.plans)
      setStats(s)
      // Fetch names for all unique symbols (batched, 24h cached on server)
      const unique = [...new Set(p.plans.map((pl) => pl.symbol))]
      if (unique.length > 0) {
        api.tickerNames(unique).then((r) => setNames(r.names)).catch(() => {})
      }
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  const loadAutoLog = useCallback(async () => {
    setAutoLogLoading(true)
    setAutoLogError(null)
    try {
      const r = await api.paperAutoSelectLog(50)
      setAutoLog(r.runs)
      const unique = [...new Set(r.runs.flatMap((run) => run.selections.map((s) => s.symbol)))]
      if (unique.length > 0) {
        api.tickerNames(unique).then((r2) => setNames((n) => ({ ...n, ...r2.names }))).catch(() => {})
      }
    } catch (e) {
      setAutoLogError((e as Error).message)
    } finally {
      setAutoLogLoading(false)
    }
  }, [])

  useEffect(() => { load(filter) }, [filter, load])
  useEffect(() => { if (view === 'autoLog') loadAutoLog() }, [view, loadAutoLog])

  const openCount = plans.filter((p) => p.status === 'open').length
  const displayedPlans = sourceFilter === 'all'
    ? plans
    : plans.filter((p) => bucketOfTags(p.source_tags) === sourceFilter)
  const bucket = stats ? stats[sourceFilter] : null

  return (
    <div>
      <h1>Trade Journal</h1>
      <div className="stale-banner" style={{ borderColor: '#58a6ff', color: '#58a6ff', background: 'rgba(88,166,255,0.08)' }}>
        {view === 'trades'
          ? 'Every paper trade the engine opened — why it was picked, entry thesis, exit levels, analyst rationale, and realized P&L on close.'
          : 'Every 10:05 AM / 4:05 PM auto-trader run — candidates gathered, what was selected and why, and anything skipped or errored.'}
      </div>

      <div className="controls" style={{ marginBottom: 10 }}>
        {(['trades', 'autoLog'] as View[]).map((v) => (
          <button key={v} className={view === v ? 'primary' : ''} onClick={() => setView(v)}>
            {v === 'trades' ? 'Trades' : 'Auto-Trader Log'}
          </button>
        ))}
      </div>

      {view === 'trades' && (
      <>
      {error && <div className="error-banner">{error}</div>}

      <div className="controls" style={{ marginBottom: 10 }}>
        {(['all', 'smartBuy', 'technicalSustained'] as SourceFilter[]).map((f) => (
          <button key={f} className={sourceFilter === f ? 'primary' : ''}
            onClick={() => setSourceFilter(f)}>
            {SOURCE_FILTER_LABELS[f]}
          </button>
        ))}
      </div>

      {bucket && (bucket.totalClosedTrades > 0 || bucket.unrealizedPnl !== 0) && (
        <div className="tiles" style={{ marginBottom: 16 }}>
          <StatTile label="Closed trades" value={bucket.totalClosedTrades}
            tooltip="Total completed trades (stop, target, time, or signal exits) since the account began, for the selected source." />
          <StatTile
            label="Realized P&L"
            value={fmtUsd(bucket.totalRealizedPnl)}
            tone={bucket.totalRealizedPnl >= 0 ? 'pos' : 'neg'}
            tooltip="Sum of profit/loss across all closed trades for the selected source." />
          <StatTile
            label="Unrealized P&L"
            value={fmtUsd(bucket.unrealizedPnl)}
            tone={bucket.unrealizedPnl >= 0 ? 'pos' : 'neg'}
            tooltip="Mark-to-market gain/loss on currently open positions for the selected source." />
          <StatTile
            label="Win rate"
            value={bucket.winRatePct != null ? `${bucket.winRatePct}%` : '—'}
            tone={bucket.winRatePct != null && bucket.winRatePct >= 50 ? 'pos' : 'neg'}
            tooltip="Percentage of closed trades that closed at a profit." />
          <StatTile
            label="Avg win / loss"
            value={bucket.avgWinPct != null && bucket.avgLossPct != null
              ? `+${bucket.avgWinPct?.toFixed(1)}% / ${bucket.avgLossPct?.toFixed(1)}%`
              : '—'}
            tooltip="Average return on winning trades vs average return on losing trades." />
          <StatTile
            label="Profit factor"
            value={bucket.profitFactor != null ? bucket.profitFactor.toFixed(2) : '—'}
            tone={bucket.profitFactor != null && bucket.profitFactor >= 1 ? 'pos' : 'neg'}
            tooltip="Total gross profits divided by total gross losses. Above 1 means winners outweigh losers in dollar terms." />
          {bucket.sharpe !== undefined && (
            <StatTile
              label="Sharpe (est.)"
              value={bucket.sharpe != null ? bucket.sharpe.toFixed(2) : '—'}
              tone={bucket.sharpe != null && bucket.sharpe >= 1 ? 'pos' : bucket.sharpe != null && bucket.sharpe < 0 ? 'neg' : 'neutral'}
              tooltip="Sharpe ratio estimated by compounding this source's closed-trade returns in exit order — changes with the selected filter." />
          )}
          {bucket.maxDrawdownPct !== undefined && (
            <StatTile
              label="Max drawdown"
              value={bucket.maxDrawdownPct != null ? `${bucket.maxDrawdownPct}%` : '—'}
              tone="neg"
              tooltip="Largest peak-to-trough decline in this source's compounded closed-trade equity — changes with the selected filter." />
          )}
          {bucket.annualizedReturnPct !== undefined && (
            <StatTile
              label="Annualized return"
              value={bucket.annualizedReturnPct != null ? `${bucket.annualizedReturnPct >= 0 ? '+' : ''}${bucket.annualizedReturnPct}%`
                : bucket.totalReturnPct != null ? `${bucket.totalReturnPct >= 0 ? '+' : ''}${bucket.totalReturnPct}% (< 1mo)` : '—'}
              tone={(bucket.annualizedReturnPct ?? bucket.totalReturnPct ?? 0) >= 0 ? 'pos' : 'neg'}
              tooltip={`CAGR from compounding this source's closed trades in exit order, first open to last close${bucket.periodYears ? ` (${bucket.periodYears}y span)` : ''} — changes with the selected filter. Spans under 30 days show plain total return instead, since annualizing them would be misleading.`} />
          )}
          {bucket.benchmarkAnnualizedReturnPct !== undefined && (
            <StatTile
              label="S&P 500 (same period)"
              value={bucket.benchmarkAnnualizedReturnPct != null ? `${bucket.benchmarkAnnualizedReturnPct >= 0 ? '+' : ''}${bucket.benchmarkAnnualizedReturnPct}%`
                : bucket.benchmarkTotalReturnPct != null ? `${bucket.benchmarkTotalReturnPct >= 0 ? '+' : ''}${bucket.benchmarkTotalReturnPct}% (< 1mo)` : '—'}
              tone={(bucket.benchmarkAnnualizedReturnPct ?? bucket.benchmarkTotalReturnPct ?? 0) >= 0 ? 'pos' : 'neg'}
              tooltip="SPY's annualized return (CAGR) over this source's own trade-period window, for comparison." />
          )}
          {bucket.avgHoldDays != null && (
            <StatTile label="Avg hold" value={`${bucket.avgHoldDays}d`}
              tooltip="Average calendar days a closed position was held before exit." />
          )}
        </div>
      )}

      {bucket && bucket.totalClosedTrades > 0 && Object.keys(bucket.byExitReason).length > 0 && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Exits by reason
            <Tip text="How each closed trade exited: stop-loss (risk limit hit), take-profit (target reached), time-stop (held too long), or signal (strategy turned off). Realized P&L is the sum for each category, for the selected source." />
          </h2>
          <table className="data">
            <thead>
              <tr><th>Reason</th><th>Count</th><th>Wins</th><th>Realized P&amp;L</th></tr>
            </thead>
            <tbody>
              {Object.entries(bucket.byExitReason).map(([reason, row]) => (
                <tr key={reason}>
                  <td>{EXIT_LABELS[reason] ?? reason}</td>
                  <td>{row.count}</td>
                  <td>{row.wins}</td>
                  <td className={row.pnl >= 0 ? 'pos' : 'neg'}>{fmtUsd(row.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="controls" style={{ marginBottom: 10 }}>
        {(['all', 'open', 'closed'] as Filter[]).map((f) => (
          <button key={f} className={filter === f ? 'primary' : ''}
            onClick={() => { setFilter(f); setExpanded(null) }}>
            {f === 'open' ? `Open (${openCount})` : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
        {loading && <span className="muted" style={{ fontSize: 13 }}>Loading…</span>}
      </div>

      {plans.length === 0 && !loading && (
        <p className="muted">No trade plans yet — add a strategy instance on the Paper page and run the engine.</p>
      )}

      {plans.length > 0 && displayedPlans.length === 0 && (
        <p className="muted">No {SOURCE_FILTER_LABELS[sourceFilter].toLowerCase()} trades yet.</p>
      )}

      {displayedPlans.length > 0 && (
        <div className="panel">
          <table className="data">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Opened</th>
                <th>Entry</th>
                <th>Stop</th>
                <th>Target</th>
                <th>Hold</th>
                <th>Status</th>
                <th>Exit</th>
                <th>P&amp;L</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {displayedPlans.map((p) => (
                <Fragment key={p.id}>
                  <tr
                    onClick={() => setExpanded(expanded === p.id ? null : p.id)}
                    style={{ cursor: 'pointer', opacity: p.status === 'closed' ? 0.8 : 1 }}
                  >
                    <td>
                      <strong>{p.symbol}</strong>
                      {names[p.symbol] && names[p.symbol] !== p.symbol && (
                        <div style={{ fontSize: 11, color: '#8b949e', marginTop: 1, fontWeight: 400 }}>
                          {names[p.symbol]}
                        </div>
                      )}
                    </td>
                    <td>{fmtTime(p.opened_at)}</td>
                    <td>{p.entry_price.toFixed(2)}</td>
                    <td className="neg">{p.stop_loss.toFixed(2)}</td>
                    <td className="pos">{p.take_profit.toFixed(2)}</td>
                    <td>{holdDays(p)}</td>
                    <td className={p.status === 'open' ? 'pos' : 'muted'}>{p.status}</td>
                    <td>{p.exit_reason ? (EXIT_LABELS[p.exit_reason] ?? p.exit_reason) : '—'}</td>
                    <td className={p.realized_pnl == null ? '' : p.realized_pnl >= 0 ? 'pos' : 'neg'}>
                      {p.realized_pnl != null
                        ? `${fmtUsd(p.realized_pnl)} (${p.realized_pnl_pct?.toFixed(1)}%)`
                        : '—'}
                    </td>
                    <td>
                      <SourceBadges
                        tags={p.source_tags ?? []}
                        onPoliticianClick={() => setCongressModal(p.symbol)}
                      />
                    </td>
                  </tr>
                  {expanded === p.id && (
                    <tr>
                      <td colSpan={10} style={{ background: '#0d1117', textAlign: 'left' }}>
                        <div style={{ padding: '10px 6px', display: 'grid', gap: 10 }}>
                          {p.selection_thesis && (
                            <div>
                              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                                WHY THIS WAS PICKED
                              </div>
                              <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
                                {p.selection_thesis}
                              </div>
                              {p.selection_snapshot && (
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginTop: 6 }}>
                                  {p.selection_snapshot.techScore != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Score: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.techScore}</strong>
                                    </span>
                                  )}
                                  {p.selection_snapshot.streak ? (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Streak: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.streak}</strong>
                                    </span>
                                  ) : null}
                                  {p.selection_snapshot.rsi != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      RSI: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.rsi.toFixed(1)}</strong>
                                    </span>
                                  )}
                                  {p.selection_snapshot.investorQualityScore != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Win rate: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.investorQualityScore.toFixed(1)}%</strong>
                                    </span>
                                  )}
                                  {p.selection_snapshot.maxAnnualizedGain != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Best ann. return: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.maxAnnualizedGain.toFixed(1)}%</strong>
                                    </span>
                                  )}
                                  {p.selection_snapshot.buyCount != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Buys: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.buyCount}</strong>
                                    </span>
                                  )}
                                  {p.selection_snapshot.compositeScore != null && (
                                    <span className="muted" style={{ fontSize: 11 }}>
                                      Composite: <strong style={{ color: '#c9d1d9' }}>{p.selection_snapshot.compositeScore}</strong>
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                          {p.thesis && (
                            <div>
                              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>ENTRY THESIS</div>
                              <div style={{ fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{p.thesis}</div>
                            </div>
                          )}
                          {p.exit_plan && (
                            <div>
                              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>EXIT PLAN</div>
                              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{p.exit_plan}</div>
                            </div>
                          )}
                          {p.rationale && (
                            <div>
                              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                                ANALYST RATIONALE{p.model ? ` · ${p.model}` : ''}
                              </div>
                              <div style={{ fontSize: 13, lineHeight: 1.6 }}>{p.rationale}</div>
                            </div>
                          )}
                          {p.exit_price != null && (
                            <div className="muted" style={{ fontSize: 12 }}>
                              Closed {fmtTime(p.closed_at)} at {p.exit_price.toFixed(2)}
                              {p.exit_reason ? ` via ${EXIT_LABELS[p.exit_reason] ?? p.exit_reason}` : ''}
                            </div>
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </>
      )}

      {view === 'autoLog' && (
        <div>
          {autoLogError && <div className="error-banner">{autoLogError}</div>}
          {autoLogLoading && <p className="muted" style={{ fontSize: 13 }}>Loading…</p>}

          {!autoLogLoading && autoLog.length === 0 && !autoLogError && (
            <p className="muted">No auto-trader runs recorded yet — they're written every 10:05 AM / 4:05 PM cycle (or when you click Run Now).</p>
          )}

          {autoLog.map((r) => (
            <div key={r.id} className="panel" style={{ marginBottom: 12 }}>
              <div
                style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer' }}
                onClick={() => setAutoLogExpanded(autoLogExpanded === r.id ? null : r.id)}
              >
                <strong style={{ minWidth: 150 }}>{fmtDateTime(r.ts)}</strong>
                <span className="muted" style={{ fontSize: 12, minWidth: 110 }}>
                  {RUN_KIND_LABELS[r.run_kind] ?? r.run_kind}
                </span>
                <span style={{ flex: 1 }}>{runSummary(r)}</span>
                {r.selected > 0 && (
                  <span style={{ color: '#3fb950', fontWeight: 600, fontSize: 13 }}>{r.selected} picked</span>
                )}
                {r.errors.length > 0 && (
                  <span style={{ color: '#f85149', fontSize: 12 }}>{r.errors.length} error(s)</span>
                )}
              </div>

              {autoLogExpanded === r.id && (
                <div style={{ marginTop: 12, display: 'grid', gap: 12 }}>
                  {(r.equity != null || r.cash != null) && (
                    <div style={{ display: 'flex', gap: 16, fontSize: 12 }} className="muted">
                      {r.equity != null && <span>Equity: <strong style={{ color: '#c9d1d9' }}>{fmtUsd(r.equity)}</strong></span>}
                      {r.cash != null && <span>Cash: <strong style={{ color: '#c9d1d9' }}>{fmtUsd(r.cash)}</strong></span>}
                      {r.bucket_totals && (
                        <span>
                          Bucket totals: Smart Buy <strong style={{ color: '#c9d1d9' }}>{fmtUsd(r.bucket_totals.smart_buy)}</strong>
                          {' · '}Technical/Sustained <strong style={{ color: '#c9d1d9' }}>{fmtUsd(r.bucket_totals.technical_sustained)}</strong>
                        </span>
                      )}
                    </div>
                  )}

                  {r.selections.length > 0 && (
                    <table className="data" style={{ fontSize: 12 }}>
                      <thead>
                        <tr>
                          <th>Symbol</th><th>Strategy</th><th>Allocation</th><th>Score</th><th>Source</th><th>Why</th>
                        </tr>
                      </thead>
                      <tbody>
                        {r.selections.map((s, i) => (
                          <tr key={i}>
                            <td>
                              <strong>{s.symbol}</strong>
                              {names[s.symbol] && names[s.symbol] !== s.symbol && (
                                <div style={{ fontSize: 11, color: '#8b949e', fontWeight: 400 }}>{names[s.symbol]}</div>
                              )}
                            </td>
                            <td className="muted">{s.strategy}</td>
                            <td>{fmtUsd(s.allocationUsd)}</td>
                            <td>{s.compositeScore}</td>
                            <td><SourceBadges tags={s.sources} onPoliticianClick={() => setCongressModal(s.symbol)} /></td>
                            <td style={{ maxWidth: 380, whiteSpace: 'normal', lineHeight: 1.5 }}>{s.selectionThesis}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {r.errors.length > 0 && (
                    <div>
                      <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>ERRORS</div>
                      <div style={{ fontSize: 12, color: '#f85149', lineHeight: 1.6 }}>
                        {r.errors.map((e, i) => <div key={i}>{e}</div>)}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {congressModal && (
        <CongressModal symbol={congressModal} onClose={() => setCongressModal(null)} />
      )}
    </div>
  )
}

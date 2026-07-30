import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type PortfolioPosition } from '../api'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtUsd(v: number | null | undefined, fallback = '—'): string {
  if (v == null) return fallback
  return v.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })
}
function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
function scoreColor(s: number): string {
  if (s >= 3) return '#3fb950'
  if (s >= 1) return '#56d364'
  if (s <= -3) return '#f85149'
  if (s <= -1) return '#ffa198'
  return '#8b949e'
}
function trendColor(t?: string): string {
  if (t === 'uptrend') return '#3fb950'
  if (t === 'downtrend') return '#f85149'
  return '#8b949e'
}

const TIER_META = {
  core:     { label: 'Core',     bg: 'rgba(88,166,255,0.15)',  color: '#58a6ff' },
  tactical: { label: 'Tactical', bg: 'rgba(227,179,65,0.15)', color: '#e3b341' },
  trade:    { label: 'Trade',    bg: 'rgba(240,136,62,0.15)',  color: '#f0883e' },
} as const

type Tier = keyof typeof TIER_META
const TIER_CYCLE: (Tier | undefined)[] = ['core', 'tactical', 'trade', undefined]

type Tab = 'csv' | 'manual'
type SortKey = 'symbol' | 'currentValue' | 'unrealizedPnl' | 'unrealizedPnlPct' | 'rsi' | 'score' | 'weightPct'
type SortDir = 'asc' | 'desc'

interface ManualRow { symbol: string; qty: string; cost: string }

const FIDELITY_HOW_TO = `How to export from Fidelity:
1. Log in → Accounts → Positions
2. Click "Download" (CSV icon, top right of positions table)
3. Open the downloaded file in a text editor, select all, paste here.`

// ── component ─────────────────────────────────────────────────────────────────

export default function Portfolio() {
  const navigate = useNavigate()

  const [tab, setTab] = useState<Tab>('csv')
  const [csvText, setCsvText] = useState('')
  const [manualRows, setManualRows] = useState<ManualRow[]>([
    { symbol: '', qty: '', cost: '' },
  ])
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const [results, setResults] = useState<PortfolioPosition[]>([])
  const [loading, setLoading] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [imported, setImported] = useState(false)

  const hasFidelity = results.some((r) => r.source === 'fidelity' || r.source == null)
  const hasManual   = results.some((r) => r.source === 'manual')

  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'score', dir: 'desc' })

  // load and analyze persisted positions on mount
  useEffect(() => {
    api.portfolioPositions().then((r) => {
      if (r.positions.length === 0) return
      setImported(true)
      setAnalyzing(true)
      api.portfolioAnalyze()
        .then((res) => setResults(res.results))
        .catch(() => {})
        .finally(() => setAnalyzing(false))
    }).catch(() => {})
  }, [])

  // ── drag-and-drop ──────────────────────────────────────────────────────────

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault(); setDragging(false)
    const file = e.dataTransfer.files[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => setCsvText(ev.target?.result as string ?? '')
    reader.readAsText(file)
  }

  const onFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = (ev) => setCsvText(ev.target?.result as string ?? '')
    reader.readAsText(file)
  }

  // ── import ─────────────────────────────────────────────────────────────────

  const importCsv = useCallback(async () => {
    if (!csvText.trim()) return
    setLoading(true); setError(null)
    try {
      const res = await api.portfolioImportCsv(csvText)
      setResults(res.results)
      setImported(true)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [csvText])

  const importManual = useCallback(async () => {
    const positions = manualRows
      .filter((r) => r.symbol.trim())
      .map((r) => ({
        symbol: r.symbol.trim().toUpperCase(),
        qty: r.qty ? parseFloat(r.qty) : undefined,
        costPerShare: r.cost ? parseFloat(r.cost) : undefined,
      }))
    if (positions.length === 0) return
    setLoading(true); setError(null)
    try {
      const res = await api.portfolioImportManual(positions)
      setResults(res.results)
      setImported(true)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [manualRows])

  const reanalyze = async () => {
    setAnalyzing(true); setError(null)
    try {
      const res = await api.portfolioAnalyze()
      setResults(res.results)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAnalyzing(false)
    }
  }

  const clear = async (source?: 'fidelity' | 'manual') => {
    await api.portfolioClear(source)
    if (source) {
      setResults((prev) => {
        const remaining = prev.filter((r) =>
          source === 'fidelity'
            ? r.source === 'manual'
            : (r.source === 'fidelity' || r.source == null)
        )
        if (remaining.length === 0) setImported(false)
        return remaining
      })
      if (source === 'fidelity') setCsvText('')
      if (source === 'manual') setManualRows([{ symbol: '', qty: '', cost: '' }])
    } else {
      setResults([]); setImported(false); setCsvText('')
      setManualRows([{ symbol: '', qty: '', cost: '' }])
    }
  }

  // ── manual row helpers ─────────────────────────────────────────────────────

  const setManualField = (i: number, k: keyof ManualRow, v: string) =>
    setManualRows((rows) => rows.map((r, idx) => idx === i ? { ...r, [k]: v } : r))

  const addManualRow = () =>
    setManualRows((r) => [...r, { symbol: '', qty: '', cost: '' }])

  const removeManualRow = (i: number) =>
    setManualRows((r) => r.filter((_, idx) => idx !== i))

  // ── derived stats ──────────────────────────────────────────────────────────

  const equity = results.filter((r) => r.assetType !== 'option' && !r.error)
  const totalValue = equity.reduce((s, r) => s + (r.currentValue ?? 0), 0)
  const totalPnl = equity.reduce((s, r) => s + (r.unrealizedPnl ?? 0), 0)
  const totalCost = equity.reduce((s, r) => s + (r.costTotal ?? (r.qty != null && r.costPerShare != null ? r.qty! * r.costPerShare! : 0)), 0)
  const totalPnlPct = totalCost > 0 ? (totalValue / totalCost - 1) * 100 : null
  const bullish = equity.filter((r) => (r.score ?? 0) >= 2).length
  const bearish = equity.filter((r) => (r.score ?? 0) <= -2).length
  const avgScore = equity.length > 0 ? equity.reduce((s, r) => s + (r.score ?? 0), 0) / equity.length : null

  // add weight % and sort
  const sorted = useMemo(() => {
    const withWeight: (PortfolioPosition & { weightPct: number | null })[] = equity.map((r) => ({
      ...r,
      weightPct: totalValue > 0 && r.currentValue != null ? (r.currentValue / totalValue) * 100 : null,
    }))
    const { key, dir } = sort
    return [...withWeight].sort((a, b) => {
      const va = (a[key] as number | null | undefined) ?? 0
      const vb = (b[key] as number | null | undefined) ?? 0
      if (key === 'symbol') {
        return dir === 'asc' ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol)
      }
      return dir === 'asc' ? Number(va) - Number(vb) : Number(vb) - Number(va)
    })
  }, [equity, sort, totalValue])

  const optionRows = results.filter((r) => r.assetType === 'option')
  const errRows = results.filter((r) => r.error && r.assetType !== 'option')

  const toggleSort = (key: SortKey) =>
    setSort((s) => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })

  const SortTh = ({ col, label }: { col: SortKey; label: string }) => (
    <th onClick={() => toggleSort(col)} style={{ cursor: 'pointer', userSelect: 'none' }}>
      {label}{sort.key === col ? (sort.dir === 'desc' ? ' ↓' : ' ↑') : ''}
    </th>
  )

  const cycleTier = async (e: React.MouseEvent, symbol: string, current: Tier | null | undefined) => {
    e.stopPropagation()
    const idx = TIER_CYCLE.indexOf(current ?? undefined)
    const next = TIER_CYCLE[(idx + 1) % TIER_CYCLE.length]
    setResults((prev) => prev.map((r) => r.symbol === symbol ? { ...r, tier: next ?? null } : r))
    if (next) {
      await api.portfolioSetTag(symbol, next)
    } else {
      await api.portfolioDeleteTag(symbol)
    }
  }

  return (
    <div>
      <h1>My Portfolio</h1>
      <div className="stale-banner" style={{ borderColor: '#58a6ff', color: '#58a6ff', background: 'rgba(88,166,255,0.08)', marginBottom: 16 }}>
        Import your Fidelity positions to run signal analysis — trend, RSI, MACD, and score — against your actual holdings. No brokerage connection required.
      </div>

      {analyzing && results.length === 0 && (
        <div className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
          Loading saved portfolio and fetching fresh signals…
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}

      {/* ── import panel ─────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>Import positions</h2>
        <div className="controls" style={{ marginBottom: 12 }}>
          <button className={tab === 'csv' ? 'primary' : ''} onClick={() => setTab('csv')}>
            Fidelity CSV
          </button>
          <button className={tab === 'manual' ? 'primary' : ''} onClick={() => setTab('manual')}>
            Manual entry
          </button>
          {(hasFidelity || hasManual) && (
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              {hasFidelity && (
                <button style={{ fontSize: 12 }} onClick={() => clear('fidelity')}>
                  Clear Fidelity
                </button>
              )}
              {hasManual && (
                <button style={{ fontSize: 12 }} onClick={() => clear('manual')}>
                  Clear manual
                </button>
              )}
              {hasFidelity && hasManual && (
                <button style={{ fontSize: 12 }} onClick={() => clear()}>
                  Clear all
                </button>
              )}
            </div>
          )}
        </div>

        {tab === 'csv' && (
          <>
            <p style={{ fontSize: 12, color: '#8b949e', marginBottom: 8, whiteSpace: 'pre-line' }}>
              {FIDELITY_HOW_TO}
            </p>
            {/* drop zone */}
            <div
              onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => fileRef.current?.click()}
              style={{
                border: `2px dashed ${dragging ? '#58a6ff' : '#30363d'}`,
                borderRadius: 8, padding: '12px 16px', cursor: 'pointer',
                marginBottom: 8, fontSize: 13, color: '#8b949e', textAlign: 'center',
              }}
            >
              {csvText ? '✓ File loaded — or drop another to replace' : 'Drop CSV file here or click to browse'}
              <input ref={fileRef} type="file" accept=".csv,.txt" style={{ display: 'none' }}
                onChange={onFileChange} />
            </div>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder="Or paste the CSV content directly here…"
              rows={6}
              style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
            />
            <div className="controls" style={{ marginTop: 8 }}>
              <button className="primary" onClick={importCsv} disabled={loading || !csvText.trim()}>
                {loading ? 'Parsing & analyzing…' : 'Import & analyze'}
              </button>
            </div>
          </>
        )}

        {tab === 'manual' && (
          <>
            <p style={{ fontSize: 12, color: '#8b949e', marginBottom: 8 }}>
              Enter each position. Cost/share is optional but enables unrealized P&amp;L calculation.
            </p>
            <table className="data" style={{ marginBottom: 8 }}>
              <thead>
                <tr><th>Symbol</th><th>Shares</th><th>Cost/share ($)</th><th></th></tr>
              </thead>
              <tbody>
                {manualRows.map((r, i) => (
                  <tr key={i}>
                    <td>
                      <input value={r.symbol} placeholder="AAPL"
                        onChange={(e) => setManualField(i, 'symbol', e.target.value)}
                        style={{ width: 90 }} />
                    </td>
                    <td>
                      <input type="number" value={r.qty} placeholder="100" min={0}
                        onChange={(e) => setManualField(i, 'qty', e.target.value)}
                        style={{ width: 90 }} />
                    </td>
                    <td>
                      <input type="number" value={r.cost} placeholder="150.00" min={0} step={0.01}
                        onChange={(e) => setManualField(i, 'cost', e.target.value)}
                        style={{ width: 100 }} />
                    </td>
                    <td>
                      {manualRows.length > 1 && (
                        <button onClick={() => removeManualRow(i)}
                          style={{ fontSize: 12, padding: '2px 7px' }}>✕</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="controls">
              <button onClick={addManualRow} style={{ fontSize: 13 }}>+ Add row</button>
              <button className="primary" onClick={importManual}
                disabled={loading || manualRows.every((r) => !r.symbol.trim())}>
                {loading ? 'Analyzing…' : 'Import & analyze'}
              </button>
            </div>
          </>
        )}
      </div>

      {/* ── results ──────────────────────────────────────────────────────── */}
      {results.length > 0 && (
        <>
          {/* summary tiles */}
          <div className="tiles" style={{ marginBottom: 16 }}>
            <StatTile label="Positions" value={equity.length}
              tooltip="Number of equity/ETF positions analyzed." />
            {totalValue > 0 && (
              <StatTile label="Total value" value={fmtUsd(totalValue)}
                tooltip="Sum of current market value across all positions (prices from yfinance, ~15 min delayed)." />
            )}
            {totalPnl !== 0 && totalCost > 0 && (
              <StatTile label="Unrealized P&L"
                value={`${fmtUsd(totalPnl)} (${fmtPct(totalPnlPct)})`}
                tone={totalPnl >= 0 ? 'pos' : 'neg'}
                tooltip="Total unrealized gain/loss vs. imported cost basis." />
            )}
            <StatTile label="Bullish signals" value={bullish}
              tone={bullish > 0 ? 'pos' : 'neutral'}
              tooltip="Positions with signal score ≥ +2 (uptrend + at least one confirming indicator)." />
            <StatTile label="Bearish signals" value={bearish}
              tone={bearish > 0 ? 'neg' : 'neutral'}
              tooltip="Positions with signal score ≤ -2 — worth reviewing for risk." />
            {avgScore != null && (
              <StatTile label="Avg score"
                value={avgScore > 0 ? `+${avgScore.toFixed(1)}` : avgScore.toFixed(1)}
                tone={avgScore >= 1 ? 'pos' : avgScore <= -1 ? 'neg' : 'neutral'}
                tooltip="Average multi-factor signal score across your equity positions." />
            )}
          </div>

          <div className="controls" style={{ marginBottom: 10 }}>
            <button onClick={reanalyze} disabled={analyzing}>
              {analyzing ? 'Re-analyzing…' : 'Refresh signals'}
            </button>
            <Tip text="Fetches fresh quotes and re-runs signal analysis on your stored positions. Useful after market hours when yfinance cache has refreshed." />
          </div>

          {/* main table */}
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2>
              Holdings analysis
              <Tip text="Click any symbol to open Ticker Analysis. Score: same multi-factor signal score as the Screener (+2 uptrend, +1 golden cross / MACD bull / oversold RSI each; mirror for bearish). Sort any column by clicking its header." />
            </h2>
            <table className="data">
              <thead>
                <tr>
                  <SortTh col="symbol" label="Symbol" />
                  <th style={{ fontSize: 11 }}>Description</th>
                  <th>Shares</th>
                  <th>Cost/sh</th>
                  <th>Price</th>
                  <SortTh col="currentValue" label="Value" />
                  <SortTh col="unrealizedPnl" label="Unreal. P&L" />
                  <SortTh col="unrealizedPnlPct" label="%" />
                  <SortTh col="weightPct" label="Weight" />
                  <th>Trend</th>
                  <SortTh col="rsi" label="RSI" />
                  <th>MACD</th>
                  <th>BB</th>
                  <SortTh col="score" label="Score" />
                  <th title="Click to tag as Core / Tactical / Trade">Tier</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
                  <tr key={r.id}
                    onClick={() => navigate(`/ticker?sym=${r.symbol}`)}
                    style={{ cursor: 'pointer' }}
                    title="Open in Ticker Analysis">
                    <td>
                      <strong>{r.symbol}</strong>{r.stale ? ' *' : ''}
                      {r.source === 'manual' && (
                        <span style={{ fontSize: 10, color: '#8b949e', marginLeft: 4,
                          border: '1px solid #30363d', borderRadius: 3, padding: '0 3px' }}>
                          M
                        </span>
                      )}
                    </td>
                    <td className="muted" style={{ fontSize: 11, maxWidth: 150, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {r.description ?? '—'}
                    </td>
                    <td>{r.qty?.toLocaleString() ?? '—'}</td>
                    <td>{r.costPerShare?.toFixed(2) ?? '—'}</td>
                    <td>{r.currentPrice?.toFixed(2) ?? '—'}</td>
                    <td>{fmtUsd(r.currentValue)}</td>
                    <td className={r.unrealizedPnl == null ? '' : r.unrealizedPnl >= 0 ? 'pos' : 'neg'}>
                      {fmtUsd(r.unrealizedPnl)}
                    </td>
                    <td className={r.unrealizedPnlPct == null ? '' : r.unrealizedPnlPct >= 0 ? 'pos' : 'neg'}>
                      {fmtPct(r.unrealizedPnlPct)}
                    </td>
                    <td className="muted" style={{ fontSize: 12 }}>
                      {r.weightPct != null ? `${r.weightPct.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{ color: trendColor(r.trend) }}>{r.trend ?? '—'}</td>
                    <td className={r.rsi != null && r.rsi < 30 ? 'pos' : r.rsi != null && r.rsi > 70 ? 'neg' : ''}>
                      {r.rsi?.toFixed(0) ?? '—'}
                    </td>
                    <td className={r.macdAboveSignal === true ? 'pos' : r.macdAboveSignal === false ? 'neg' : ''}>
                      {r.macdAboveSignal === true ? '↑' : r.macdAboveSignal === false ? '↓' : '—'}
                    </td>
                    <td className={r.bollingerStretch != null && Math.abs(r.bollingerStretch) > 1 ? 'warn' : ''}>
                      {r.bollingerStretch?.toFixed(2) ?? '—'}
                    </td>
                    <td>
                      <strong style={{ color: scoreColor(r.score ?? 0) }}>
                        {r.score != null ? (r.score > 0 ? `+${r.score}` : r.score) : '—'}
                      </strong>
                    </td>
                    <td onClick={(e) => cycleTier(e, r.symbol, r.tier)} title="Click to cycle tier">
                      {r.tier && r.tier in TIER_META ? (
                        <span style={{
                          fontSize: 10, padding: '1px 6px', borderRadius: 3, cursor: 'pointer',
                          background: TIER_META[r.tier as Tier].bg,
                          color: TIER_META[r.tier as Tier].color,
                          border: `1px solid ${TIER_META[r.tier as Tier].color}55`,
                        }}>
                          {TIER_META[r.tier as Tier].label}
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, color: '#444c56', cursor: 'pointer' }}>+ tag</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* exit plan — only for Tactical and Trade tiers (Core = hold forever) */}
          {sorted.some((r) => r.exitPlan && r.tier !== 'core') && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <h2>
                Exit recommendations
                <Tip text="Only shown for Tactical and Trade positions — Core positions are long-term holds with no exit target. Stop loss = 2× ATR below current price. Take profit = 2:1 risk:reward. ATR = 14-day Average True Range. Not financial advice." />
              </h2>
              {sorted.some((r) => r.tier === 'core') && (
                <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
                  Core positions (e.g. index funds) are excluded — tag a position as Tactical or Trade in the table above to see exit levels.
                </p>
              )}
              <table className="data">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Tier</th>
                    <th>Current</th>
                    <th>ATR</th>
                    <th>Stop Loss</th>
                    <th>Take Profit</th>
                    <th>Trail %</th>
                    <th>$ Risk if stopped</th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.filter((r) => r.exitPlan && r.tier !== 'core').map((r) => {
                    const ep = r.exitPlan!
                    return (
                      <tr key={r.id}>
                        <td>
                          <strong>{r.symbol}</strong>
                          {r.unrealizedPnlPct != null && (
                            <span className={r.unrealizedPnlPct >= 0 ? 'pos' : 'neg'}
                              style={{ fontSize: 11, marginLeft: 6 }}>
                              {fmtPct(r.unrealizedPnlPct)}
                            </span>
                          )}
                        </td>
                        <td>
                          {r.tier && r.tier in TIER_META && (
                            <span style={{
                              fontSize: 10, padding: '1px 6px', borderRadius: 3,
                              background: TIER_META[r.tier as Tier].bg,
                              color: TIER_META[r.tier as Tier].color,
                            }}>
                              {TIER_META[r.tier as Tier].label}
                            </span>
                          )}
                        </td>
                        <td>${r.currentPrice?.toFixed(2) ?? '—'}</td>
                        <td className="muted" style={{ fontSize: 12 }}>${ep.atr.toFixed(2)}</td>
                        <td>
                          <span className="neg">${ep.stopLoss.toFixed(2)}</span>
                          <span className="muted" style={{ fontSize: 11, marginLeft: 4 }}>
                            ({ep.stopLossPct.toFixed(1)}%)
                          </span>
                        </td>
                        <td>
                          <span className="pos">${ep.takeProfit.toFixed(2)}</span>
                          <span className="muted" style={{ fontSize: 11, marginLeft: 4 }}>
                            (+{ep.takeProfitPct.toFixed(1)}%)
                          </span>
                        </td>
                        <td className="muted">{ep.trailingStopPct.toFixed(1)}%</td>
                        <td className="neg" style={{ fontSize: 12 }}>
                          {ep.dollarRiskIfStopped != null ? fmtUsd(-Math.abs(ep.dollarRiskIfStopped)) : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 11, marginTop: 8 }}>
                Stop = 2× ATR below current price &nbsp;·&nbsp; Target = 2:1 risk:reward &nbsp;·&nbsp; Volatility-based levels, not personalized advice.
              </p>
            </div>
          )}

          {/* options table */}
          {optionRows.length > 0 && (
            <div className="panel" style={{ marginBottom: 16 }}>
              <h2>Options positions <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>(signal analysis not available)</span></h2>
              <table className="data">
                <thead><tr><th>Symbol</th><th>Description</th><th>Qty</th></tr></thead>
                <tbody>
                  {optionRows.map((r) => (
                    <tr key={r.id}>
                      <td><strong>{r.symbol}</strong></td>
                      <td className="muted">{r.description ?? '—'}</td>
                      <td>{r.qty ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* errors */}
          {errRows.length > 0 && (
            <div className="muted" style={{ fontSize: 12 }}>
              Could not fetch data for: {errRows.map((r) => `${r.symbol} (${r.error})`).join(' · ')}
            </div>
          )}
        </>
      )}
    </div>
  )
}

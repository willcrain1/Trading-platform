import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  api,
  type AutoDeployResult, type AutoSelectResult, type AutoSelectStatus, type BatchCell,
  type CongressTicker, type CongressUniverse,
  type ScanResult, type ScanWatchlistItem, type SectorScore, type SmartBuyAlert,
  type SustainedSignal, type UniverseResult,
} from '../api'
import Tip from '../components/Tip'

// ── types ──────────────────────────────────────────────────────────────────

type SortKey = 'score' | 'symbol' | 'last' | 'rsi' | 'atrPct'
type SortDir = 'asc' | 'desc'
type CongressSortKey = 'ticker' | 'opportunity' | 'txToDisclosure' | 'sinceDisclosed' | 'totalMove' | 'politicians' | 'buys' | 'investorQuality' | 'score'
type SustainedSortKey = 'symbol' | 'streak' | 'score' | 'rsi' | 'price'

interface Filters {
  uptrend: boolean; downtrend: boolean; oversold: boolean; overbought: boolean
  macdBull: boolean; macdBear: boolean; withCross: boolean
}

interface Watchlist { name: string; tickers: string }

// ── helpers ─────────────────────────────────────────────────────────────────

const STORAGE_KEY = 'screener_watchlists'
const INITIAL_FILTERS: Filters = {
  uptrend: false, downtrend: false, oversold: false, overbought: false,
  macdBull: false, macdBear: false, withCross: false,
}
const PRESET_LABELS: Record<string, string> = {
  macro: 'Macro', sectors: 'Sectors', megacap: 'Mega-Cap', rates: 'Rates',
}

function scoreColor(s: number): string {
  if (s >= 3) return '#3fb950'
  if (s >= 1) return '#56d364'
  if (s <= -3) return '#f85149'
  if (s <= -1) return '#ffa198'
  return '#8b949e'
}
function trendColor(t?: string | null): string {
  if (t === 'uptrend') return '#3fb950'
  if (t === 'downtrend') return '#f85149'
  return '#8b949e'
}
function sharpeColor(v: number | null | undefined): string {
  if (v == null) return '#8b949e'
  if (v >= 1) return '#3fb950'
  if (v >= 0.5) return '#d29922'
  if (v >= 0.2) return '#e3b341'
  if (v > 0) return '#8b949e'
  return '#f85149'
}
function pctColor(v: number | null): string {
  if (v == null) return '#8b949e'
  if (v >= 5) return '#3fb950'
  if (v >= 0) return '#56d364'
  if (v >= -5) return '#ffa198'
  return '#f85149'
}

function loadWatchlists(): Watchlist[] {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]') } catch { return [] }
}
function saveWatchlists(lists: Watchlist[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(lists))
}

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function fmtPct(v: number | null): string {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '') + v.toFixed(1) + '%'
}

// ── MomentumBar ───────────────────────────────────────────────────────────────

function MomentumBar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(Math.abs(value) / max, 1) * 100 : 0
  const positive = value >= 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 60, height: 8, background: '#21262d', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{
          width: `${pct}%`, height: '100%',
          background: positive ? '#3fb950' : '#f85149',
          borderRadius: 4,
        }} />
      </div>
      <span style={{ fontSize: 12, color: pctColor(value), minWidth: 46 }}>
        {fmtPct(value)}
      </span>
    </div>
  )
}

// ── ScoreSparkline ─────────────────────────────────────────────────────────────

function ScoreSparkline({ history }: { history: { ts: number; score: number }[] }) {
  const pts = [...history].reverse().slice(-20)
  if (pts.length === 0) return <span className="muted">—</span>
  const w = 80, h = 24, barW = Math.max(2, Math.floor(w / pts.length) - 1)
  const mid = h / 2
  const scale = (h / 2 - 2) / 5
  return (
    <svg width={w} height={h} style={{ display: 'block', overflow: 'visible' }}>
      <line x1={0} y1={mid} x2={w} y2={mid} stroke="#30363d" strokeWidth={1} />
      {pts.map((p, i) => {
        const x = i * (barW + 1)
        const barH = Math.abs(p.score) * scale
        const y = p.score >= 0 ? mid - barH : mid
        return (
          <rect key={i} x={x} y={y} width={barW} height={Math.max(barH, 1)}
            fill={scoreColor(p.score)} opacity={0.85} rx={1} />
        )
      })}
    </svg>
  )
}

// ── component ─────────────────────────────────────────────────────────────────

export default function Screener() {
  const navigate = useNavigate()

  // universe + scan
  const [presets, setPresets] = useState<Record<string, string[]>>({})
  const [tickerInput, setTickerInput] = useState('')
  const [results, setResults] = useState<ScanResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // table controls
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'score', dir: 'desc' })
  const [filters, setFilters] = useState<Filters>(INITIAL_FILTERS)
  const [congressSort, setCongressSort] = useState<{ key: CongressSortKey; dir: SortDir }>({ key: 'politicians', dir: 'desc' })
  const [sustainedSort, setSustainedSort] = useState<{ key: SustainedSortKey; dir: SortDir }>({ key: 'streak', dir: 'desc' })

  // saved watchlists (localStorage)
  const [watchlists, setWatchlists] = useState<Watchlist[]>(loadWatchlists)
  const [newListName, setNewListName] = useState('')
  const [showSaveInput, setShowSaveInput] = useState(false)

  // smart universe (sector rotation)
  const [universe, setUniverse] = useState<UniverseResult | null>(null)
  const [universeLoading, setUniverseLoading] = useState(false)
  const [universeError, setUniverseError] = useState<string | null>(null)
  const [topN, setTopN] = useState(2)
  const [showAllSectors, setShowAllSectors] = useState(false)

  // signal tracker (server-side)
  const [trackedSymbols, setTrackedSymbols] = useState<ScanWatchlistItem[]>([])
  const [sustained, setSustained] = useState<SustainedSignal[]>([])
  const [trackerLoading, setTrackerLoading] = useState(false)
  const [trackerError, setTrackerError] = useState<string | null>(null)
  const [addTrackerInput, setAddTrackerInput] = useState('')
  const [minStreak, setMinStreak] = useState(3)
  const [minScore, setMinScore] = useState(2)
  const [scanningNow, setScanningNow] = useState(false)

  // deploy
  const [deployTarget, setDeployTarget] = useState<string | null>(null)
  const [deployAlloc, setDeployAlloc] = useState(10000)
  const [deployPortfolioId, setDeployPortfolioId] = useState<string>('technical_sustained')
  const [deploying, setDeploying] = useState(false)
  const [deployResult, setDeployResult] = useState<AutoDeployResult | null>(null)
  const [deployError, setDeployError] = useState<string | null>(null)

  // politician trades
  const [congress, setCongress] = useState<CongressUniverse | null>(null)
  const [congressLoading, setCongressLoading] = useState(false)
  const [congressError, setCongressError] = useState<string | null>(null)
  const [congressDays, setCongressDays] = useState(90)
  const [congressMinBuys, setCongressMinBuys] = useState(2)
  const [congressChamber, setCongressChamber] = useState('both')
  const [congressExpanded, setCongressExpanded] = useState<string | null>(null)
  const [congressHasKey, setCongressHasKey] = useState<boolean | null>(null)
  const [congressHasImport, setCongressHasImport] = useState(false)
  const [csvText, setCsvText] = useState('')
  const [importing, setImporting] = useState(false)
  const [csvError, setCsvError] = useState<string | null>(null)
  const [showCsvForm, setShowCsvForm] = useState(false)
  const [politicianModal, setPoliticianModal] = useState<string | null>(null)

  // smart buy monitor
  const [smartBuys, setSmartBuys] = useState<SmartBuyAlert[]>([])
  const [smartBuysLoading, setSmartBuysLoading] = useState(false)
  const [smartBuysError, setSmartBuysError] = useState<string | null>(null)
  const [smartBuysLoaded, setSmartBuysLoaded] = useState(false)

  // auto-trade engine
  const [autoSelectStatus, setAutoSelectStatus] = useState<AutoSelectStatus | null>(null)
  const [autoSelectLoading, setAutoSelectLoading] = useState(false)
  const [autoSelectRunning, setAutoSelectRunning] = useState(false)
  const [autoSelectError, setAutoSelectError] = useState<string | null>(null)

  // company names
  const [names, setNames] = useState<Record<string, string>>({})

  // batch backtest
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchError, setBatchError] = useState<string | null>(null)
  const [batchData, setBatchData] = useState<{
    results: BatchCell[]
    tickers: string[]
    strategies: string[]
    strategyLabels: Record<string, string>
  } | null>(null)
  const [batchPeriod, setBatchPeriod] = useState('2y')

  // ── on mount ─────────────────────────────────────────────────────────────

  useEffect(() => {
    api.scanPresets().then((r) => setPresets(r.presets)).catch(() => {})
    loadTracker()
  }, [])

  // ── smart universe ────────────────────────────────────────────────────────

  const loadUniverse = useCallback(async (n = topN, refresh = false): Promise<UniverseResult | null> => {
    setUniverseLoading(true); setUniverseError(null)
    try {
      const res = await api.universe(n, refresh)
      setUniverse(res)
      return res
    } catch (e) {
      setUniverseError((e as Error).message)
      return null
    } finally {
      setUniverseLoading(false)
    }
  }, [topN])

  const loadAndScan = async () => {
    const res = await loadUniverse(topN, false)
    if (!res || res.universe.length === 0) return
    setTickerInput(res.universe.join(', '))
    setLoading(true); setError(null); setResults([]); setBatchData(null)
    try {
      const scanRes = await api.scanTickers(res.universe)
      setResults(scanRes.results)
      mergeNames(scanRes.results.map((r) => r.symbol))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const addUniverseToTracker = async (res: UniverseResult) => {
    await api.addToScanWatchlist(res.universe)
    loadTracker()
  }

  // ── politician trades ─────────────────────────────────────────────────────

  const loadCongress = async (forceRefresh = false) => {
    setCongressLoading(true); setCongressError(null)
    try {
      if (forceRefresh) await api.congressRefresh()
      const res = await api.congressUniverse(congressDays, congressMinBuys, congressChamber)
      setCongressHasKey(res.cacheInfo.hasKey)
      setCongress(res)
    } catch (e) {
      setCongressError((e as Error).message)
    } finally {
      setCongressLoading(false)
    }
  }

  const loadAutoSelectStatus = async () => {
    setAutoSelectLoading(true); setAutoSelectError(null)
    try {
      const res = await api.autoSelectStatus()
      setAutoSelectStatus(res)
      mergeNames((res.lastResult?.selections ?? []).map((s) => s.symbol))
    } catch (e) {
      setAutoSelectError((e as Error).message)
    } finally {
      setAutoSelectLoading(false)
    }
  }

  const runAutoSelectNow = async () => {
    setAutoSelectRunning(true); setAutoSelectError(null)
    try {
      const res = await api.autoSelectRunNow()
      setAutoSelectStatus((prev) => prev ? { ...prev, lastResult: res } : { lastResult: res, scheduledJobs: [] })
      mergeNames((res.selections ?? []).map((s) => s.symbol))
    } catch (e) {
      setAutoSelectError((e as Error).message)
    } finally {
      setAutoSelectRunning(false)
    }
  }

  const loadSmartBuys = async () => {
    setSmartBuysLoading(true); setSmartBuysError(null)
    try {
      const res = await api.scanSmartBuys(30)
      setSmartBuys(res.alerts)
      setSmartBuysLoaded(true)
      mergeNames(res.alerts.map((a) => a.ticker))
    } catch (e) {
      setSmartBuysError((e as Error).message)
    } finally {
      setSmartBuysLoading(false)
    }
  }

  const importCsv = async () => {
    if (!csvText.trim()) return
    setImporting(true); setCsvError(null)
    try {
      const res = await api.congressImportCsv(csvText)
      if (res.ok) {
        setCongressHasImport(true)
        setCsvText('')
        setShowCsvForm(false)
        await loadCongress(false)
      } else {
        setCsvError(res.error ?? 'Import failed')
      }
    } catch (e) {
      setCsvError((e as Error).message)
    } finally {
      setImporting(false)
    }
  }

  const clearImport = async () => {
    await api.congressClearImport()
    setCongressHasImport(false)
    setCongress(null)
  }

  // Check key + import status on first render
  useEffect(() => {
    api.congressConfig().then((r) => {
      setCongressHasKey(r.hasKey)
      setCongressHasImport(r.hasImport)
    }).catch(() => {})
  }, [])

  // ── signal tracker ────────────────────────────────────────────────────────

  const loadTracker = useCallback(async () => {
    setTrackerLoading(true); setTrackerError(null)
    try {
      const [wl, sus] = await Promise.all([
        api.scanWatchlist(),
        api.sustainedSignals(minScore, minStreak),
      ])
      setTrackedSymbols(wl.symbols)
      setSustained(sus.results)
      mergeNames([...wl.symbols.map((s) => s.symbol), ...sus.results.map((r) => r.symbol)])
    } catch (e) {
      setTrackerError((e as Error).message)
    } finally {
      setTrackerLoading(false)
    }
  }, [minScore, minStreak])

  useEffect(() => { loadTracker() }, [minScore, minStreak])

  const addToTracker = async () => {
    const syms = addTrackerInput.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean)
    if (syms.length === 0) return
    await api.addToScanWatchlist(syms)
    setAddTrackerInput('')
    loadTracker()
  }

  const removeFromTracker = async (sym: string) => {
    await api.removeFromScanWatchlist(sym)
    setTrackedSymbols((prev) => prev.filter((s) => s.symbol !== sym))
    setSustained((prev) => prev.filter((s) => s.symbol !== sym))
  }

  const scanNow = async () => {
    setScanningNow(true); setTrackerError(null)
    try {
      await api.runScheduledScan()
      await loadTracker()
    } catch (e) {
      setTrackerError((e as Error).message)
    } finally {
      setScanningNow(false)
    }
  }

  const trackFromResult = async (symbol: string) => {
    await api.addToScanWatchlist([symbol])
    loadTracker()
  }

  // ── saved watchlists ──────────────────────────────────────────────────────

  const saveCurrentList = () => {
    if (!newListName.trim() || !tickerInput.trim()) return
    const updated = [
      ...watchlists.filter((w) => w.name !== newListName.trim()),
      { name: newListName.trim(), tickers: tickerInput },
    ]
    setWatchlists(updated)
    saveWatchlists(updated)
    setNewListName('')
    setShowSaveInput(false)
  }

  const deleteWatchlist = (name: string) => {
    const updated = watchlists.filter((w) => w.name !== name)
    setWatchlists(updated)
    saveWatchlists(updated)
  }

  // ── scan ──────────────────────────────────────────────────────────────────

  const parseTickers = (input: string) =>
    input.split(/[\s,]+/).map((t) => t.trim().toUpperCase()).filter(Boolean)

  const runScan = useCallback(async (overrideTickers?: string[]) => {
    const tickers = overrideTickers ?? parseTickers(tickerInput)
    if (tickers.length === 0) return
    setLoading(true); setError(null); setResults([]); setBatchData(null)
    try {
      const res = await api.scanTickers(tickers)
      setResults(res.results)
      mergeNames(res.results.map((r) => r.symbol))
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [tickerInput])

  // ── deploy ────────────────────────────────────────────────────────────────

  const doDeploy = async () => {
    if (!deployTarget) return
    setDeploying(true); setDeployError(null); setDeployResult(null)
    try {
      const res = await api.autoDeploy(deployTarget, deployAlloc, deployPortfolioId)
      setDeployResult(res)
    } catch (e) {
      setDeployError((e as Error).message)
    } finally {
      setDeploying(false)
    }
  }

  // ── batch backtest ────────────────────────────────────────────────────────

  const runBatch = async () => {
    const tickers = results.filter((r) => !r.error).map((r) => r.symbol)
    if (tickers.length === 0) return
    setBatchLoading(true); setBatchError(null); setBatchData(null)
    try {
      const res = await api.batchBacktest(tickers, undefined, batchPeriod)
      setBatchData(res)
      mergeNames(res.tickers)
    } catch (e) {
      setBatchError((e as Error).message)
    } finally {
      setBatchLoading(false)
    }
  }

  // ── derived state ─────────────────────────────────────────────────────────

  const filtered = useMemo(() => {
    let rows = results.filter((r) => !r.error)
    if (filters.uptrend) rows = rows.filter((r) => r.trend === 'uptrend')
    if (filters.downtrend) rows = rows.filter((r) => r.trend === 'downtrend')
    if (filters.oversold) rows = rows.filter((r) => r.rsi != null && r.rsi < 30)
    if (filters.overbought) rows = rows.filter((r) => r.rsi != null && r.rsi > 70)
    if (filters.macdBull) rows = rows.filter((r) => r.macdAboveSignal === true)
    if (filters.macdBear) rows = rows.filter((r) => r.macdAboveSignal === false)
    if (filters.withCross) rows = rows.filter((r) => r.recentCross != null)
    const { key, dir } = sort
    return [...rows].sort((a, b) => {
      if (key === 'symbol') {
        return dir === 'asc' ? a.symbol.localeCompare(b.symbol) : b.symbol.localeCompare(a.symbol)
      }
      const va = (a[key] as number | undefined) ?? 0
      const vb = (b[key] as number | undefined) ?? 0
      return dir === 'asc' ? va - vb : vb - va
    })
  }, [results, filters, sort])

  const OPP_ORDER: Record<string, number> = { available: 0, pullback: 1, fading: 2, gone: 3, unknown: 4 }

  const sortedCongressTickers = useMemo(() => {
    if (!congress) return []
    const { key, dir } = congressSort
    const mul = dir === 'asc' ? 1 : -1
    return [...congress.tickers].sort((a, b) => {
      switch (key) {
        case 'ticker':     return mul * a.ticker.localeCompare(b.ticker)
        case 'opportunity': return mul * ((OPP_ORDER[a.opportunity ?? 'unknown'] ?? 4) - (OPP_ORDER[b.opportunity ?? 'unknown'] ?? 4))
        case 'txToDisclosure': return mul * ((a.txToDisclosurePct ?? -Infinity) - (b.txToDisclosurePct ?? -Infinity))
        case 'sinceDisclosed': return mul * ((a.disclosureToNowPct ?? -Infinity) - (b.disclosureToNowPct ?? -Infinity))
        case 'totalMove': return mul * ((a.totalMovePct ?? -Infinity) - (b.totalMovePct ?? -Infinity))
        case 'politicians':     return mul * (a.uniquePoliticians - b.uniquePoliticians)
        case 'buys':            return mul * (a.buyCount - b.buyCount)
        case 'investorQuality': return mul * ((a.investorQualityScore ?? -1) - (b.investorQualityScore ?? -1))
        case 'score':           return mul * ((a.score ?? -99) - (b.score ?? -99))
        default:                return 0
      }
    })
  }, [congress, congressSort])

  const sortedSustained = useMemo(() => {
    const { key, dir } = sustainedSort
    const mul = dir === 'asc' ? 1 : -1
    return [...sustained].sort((a, b) => {
      switch (key) {
        case 'symbol': return mul * a.symbol.localeCompare(b.symbol)
        case 'streak': return mul * (a.streak - b.streak)
        case 'score':  return mul * (a.score - b.score)
        case 'rsi':    return mul * ((a.rsi ?? -Infinity) - (b.rsi ?? -Infinity))
        case 'price':  return mul * ((a.lastPrice ?? -Infinity) - (b.lastPrice ?? -Infinity))
        default:       return 0
      }
    })
  }, [sustained, sustainedSort])

  const funnelCounts = useMemo(() => {
    const ok = results.filter((r) => !r.error)
    return {
      total: ok.length,
      uptrend: ok.filter((r) => r.trend === 'uptrend').length,
      macdBull: ok.filter((r) => r.macdAboveSignal === true).length,
      bullScore: ok.filter((r) => r.score >= 2).length,
    }
  }, [results])

  const batchGrid = useMemo(() => {
    if (!batchData) return {}
    const m: Record<string, Record<string, BatchCell>> = {}
    for (const cell of batchData.results) {
      if (!m[cell.symbol]) m[cell.symbol] = {}
      m[cell.symbol][cell.strategy] = cell
    }
    return m
  }, [batchData])

  const bestStrategy = useMemo(() => {
    if (!batchData) return {}
    const best: Record<string, string> = {}
    for (const sym of batchData.tickers) {
      const cells = batchData.strategies
        .map((s) => batchGrid[sym]?.[s])
        .filter((c): c is BatchCell => c != null && c.sharpe != null && c.sharpe > 0)
      if (cells.length) best[sym] = cells.reduce((a, b) => (a.sharpe! > b.sharpe! ? a : b)).strategy
    }
    return best
  }, [batchGrid, batchData])

  const momentumMax = useMemo(() => {
    if (!universe) return 1
    return Math.max(1, ...universe.sectors.map((s) => Math.abs(s.momentum)))
  }, [universe])

  const visibleSectors: SectorScore[] = useMemo(() => {
    if (!universe) return []
    return showAllSectors ? universe.sectors : universe.sectors.filter((s) => s.selected)
  }, [universe, showAllSectors])

  const toggleSort = (key: SortKey) =>
    setSort((s) => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })

  const SortTh = ({ col, label, title }: { col: SortKey; label: string; title?: string }) => (
    <th onClick={() => toggleSort(col)} title={title} style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
      {label} {sort.key === col ? (sort.dir === 'desc' ? '↓' : '↑') : <span style={{ opacity: 0.3 }}>↕</span>}
    </th>
  )

  const toggleCongressSort = (key: CongressSortKey) =>
    setCongressSort((s) => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })

  const CSortTh = ({ col, label, title }: { col: CongressSortKey; label: string; title?: string }) => (
    <th onClick={() => toggleCongressSort(col)} title={title}
      style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
      {label} {congressSort.key === col ? (congressSort.dir === 'desc' ? '↓' : '↑') : <span style={{ opacity: 0.3 }}>↕</span>}
    </th>
  )

  const toggleSustainedSort = (key: SustainedSortKey) =>
    setSustainedSort((s) => s.key === key ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' } : { key, dir: 'desc' })

  const SSortTh = ({ col, label }: { col: SustainedSortKey; label: string }) => (
    <th onClick={() => toggleSustainedSort(col)} style={{ cursor: 'pointer', userSelect: 'none', whiteSpace: 'nowrap' }}>
      {label} {sustainedSort.key === col ? (sustainedSort.dir === 'desc' ? '↓' : '↑') : <span style={{ opacity: 0.3 }}>↕</span>}
    </th>
  )

  const DeployCell = ({ symbol }: { symbol: string }) => (
    deployTarget === symbol ? (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        <span className="muted" style={{ fontSize: 11 }}>$</span>
        <input type="number" value={deployAlloc} min={100} step={1000}
          onChange={(e) => setDeployAlloc(Number(e.target.value))}
          style={{ width: 80, fontSize: 12 }} />
        <select value={deployPortfolioId} onChange={(e) => setDeployPortfolioId(e.target.value)}
          style={{ fontSize: 12 }}>
          <option value="smart_buy">Smart Buy</option>
          <option value="technical_sustained">Technical/Sustained</option>
        </select>
        <button className="primary" onClick={doDeploy}
          disabled={deploying} style={{ fontSize: 12, padding: '3px 8px' }}>
          {deploying ? '…' : '✓'}
        </button>
        <button onClick={() => { setDeployTarget(null); setDeployError(null) }}
          style={{ fontSize: 12, padding: '3px 6px' }}>✕</button>
      </span>
    ) : (
      <button style={{ fontSize: 12, padding: '3px 8px' }}
        onClick={() => { setDeployTarget(symbol); setDeployResult(null); setDeployError(null) }}>
        Deploy
      </button>
    )
  )

  const mergeNames = useCallback((syms: string[]) => {
    const unique = [...new Set(syms.filter(Boolean))]
    if (unique.length === 0) return
    api.tickerNames(unique).then((r) => setNames((n) => ({ ...n, ...r.names }))).catch(() => {})
  }, [])

  const NameSub = ({ sym }: { sym: string }) => {
    const name = names[sym]
    if (!name || name === sym) return null
    return <div style={{ fontSize: 11, color: '#8b949e', marginTop: 1, fontWeight: 400, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>{name}</div>
  }

  const errors = results.filter((r) => r.error)
  const isTracked = (sym: string) => trackedSymbols.some((s) => s.symbol === sym)

  return (
    <div>
      <h1>Ticker Screener</h1>

      {/* ── smart universe ─────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Smart Universe
          <Tip text="Ranks all 11 SPDR sector ETFs by a blended momentum score (40% 1-month return + 60% 3-month return). The top sectors' holdings become your candidate universe — so you only analyze stocks in regimes that are actually working. Results cache for 4 hours." />
        </h2>

        <div className="controls" style={{ marginBottom: 12 }}>
          <span style={{ fontSize: 13 }}>Top</span>
          <select value={topN} onChange={(e) => setTopN(Number(e.target.value))}
            style={{ width: 50, fontSize: 13 }}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <span style={{ fontSize: 13 }}>sector{topN > 1 ? 's' : ''}</span>
          <button onClick={() => loadUniverse(topN, false)} disabled={universeLoading}>
            {universeLoading ? 'Loading…' : universe ? 'Refresh' : 'Build universe'}
          </button>
          {universe && (
            <>
              <button onClick={() => loadUniverse(topN, true)} disabled={universeLoading}
                style={{ opacity: 0.7 }} title="Force re-fetch (ignores 4h cache)">
                Force refresh
              </button>
              <button onClick={() => setTickerInput(universe.universe.join(', '))}>
                Load into scanner
              </button>
              <button className="primary" onClick={loadAndScan} disabled={universeLoading || loading}>
                {loading ? 'Scanning…' : 'Load + Scan'}
              </button>
              <button onClick={() => addUniverseToTracker(universe)}
                title="Add all universe tickers to Signal Tracker for daily monitoring">
                + Track all
              </button>
            </>
          )}
        </div>

        {universeError && <div className="error-banner" style={{ marginBottom: 10 }}>{universeError}</div>}

        {universe && (
          <>
            <table className="data" style={{ marginBottom: 10 }}>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Sector</th>
                  <th>ETF</th>
                  <th>
                    Momentum
                    <Tip text="40% × 1-month return + 60% × 3-month return — primary ranking signal." />
                  </th>
                  <th>1m</th>
                  <th>3m</th>
                  <th>Signal score</th>
                  <th>Trend</th>
                  <th>Holdings</th>
                </tr>
              </thead>
              <tbody>
                {visibleSectors.map((s) => (
                  <tr key={s.etf} style={{
                    background: s.selected ? 'rgba(63,185,80,0.07)' : undefined,
                    opacity: s.error ? 0.5 : 1,
                  }}>
                    <td>
                      <strong style={{ color: s.selected ? '#3fb950' : '#8b949e' }}>
                        {s.selected ? '★' : s.rank}
                      </strong>
                    </td>
                    <td>
                      <strong style={{ color: s.selected ? '#e6edf3' : '#8b949e' }}>
                        {s.name}
                      </strong>
                    </td>
                    <td>
                      <span style={{ cursor: 'pointer', color: '#58a6ff' }}
                        onClick={() => navigate(`/ticker?sym=${s.etf}`)}>
                        {s.etf}
                      </span>
                    </td>
                    <td><MomentumBar value={s.momentum} max={momentumMax} /></td>
                    <td style={{ color: pctColor(s.ret20) }}>{fmtPct(s.ret20)}</td>
                    <td style={{ color: pctColor(s.ret60) }}>{fmtPct(s.ret60)}</td>
                    <td>
                      <strong style={{ color: scoreColor(s.score) }}>
                        {s.score > 0 ? `+${s.score}` : s.score}
                      </strong>
                    </td>
                    <td style={{ color: trendColor(s.trend) }}>{s.trend ?? '—'}</td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {s.selected ? s.holdings.join(', ') : `${s.holdings.length} names`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="controls">
              <button style={{ fontSize: 12, opacity: 0.7 }}
                onClick={() => setShowAllSectors((v) => !v)}>
                {showAllSectors ? 'Show selected only' : 'Show all 11 sectors'}
              </button>
              <span className="muted" style={{ fontSize: 11 }}>
                Built {fmtTime(universe.builtAt)} · {universe.universe.length} tickers
                {universe.sectors.some((s) => s.stale) ? ' · some data stale' : ''}
              </span>
            </div>

            {universe.universe.length > 0 && (
              <div style={{
                marginTop: 10, padding: '8px 12px',
                background: '#161b22', borderRadius: 6,
                border: '1px solid #21262d', fontSize: 12,
              }}>
                <span className="muted">Universe ({universe.universe.length}): </span>
                {universe.universe.map((t, i) => (
                  <span key={t}>
                    <span style={{ cursor: 'pointer', color: '#58a6ff' }}
                      onClick={() => navigate(`/ticker?sym=${t}`)}>
                      {t}
                    </span>
                    {i < universe.universe.length - 1 && <span className="muted"> · </span>}
                  </span>
                ))}
              </div>
            )}
          </>
        )}

        {!universe && !universeLoading && (
          <p className="muted" style={{ fontSize: 13 }}>
            Click "Build universe" to rank all 11 sectors by momentum and get a ready-to-scan
            list of tickers concentrated in the leading regimes.
          </p>
        )}
      </div>

      {/* ── politician trades universe ────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Politician Trades Universe
          <Tip text="Tickers recently purchased by members of Congress under STOCK Act disclosure requirements. Data scraped from capitoltrades.com (free, no account needed). Politicians must disclose trades within 45 days, so this signal is somewhat lagged — use it as a confirmation layer, not a timing signal." />
        </h2>

        {congressError && <div className="error-banner" style={{ marginBottom: 10 }}>{congressError}</div>}

        {/* ── CSV import fallback (shown only when user explicitly wants manual data) ── */}
        {showCsvForm && (
          <div style={{ padding: '12px 16px', background: '#161b22', borderRadius: 6, border: '1px solid #21262d', marginBottom: 12 }}>
            <p style={{ margin: '0 0 8px', fontSize: 13 }}>
              <strong>Import CSV manually</strong>
              <span style={{ marginLeft: 8, fontSize: 12, color: '#8b949e' }}>
                Export from{' '}
                <a href="https://www.capitoltrades.com/trades" target="_blank" rel="noreferrer" style={{ color: '#58a6ff' }}>capitoltrades.com</a>
                {' '}or paste any CSV with Ticker, Politician, Transaction, Date columns.
              </span>
            </p>
            <textarea
              value={csvText}
              onChange={(e) => setCsvText(e.target.value)}
              placeholder={'Paste CSV with headers here…\nExpected columns: Ticker, Politician, Transaction, Date, Report Date, Amount (any order)'}
              style={{ width: '100%', height: 120, fontSize: 12, fontFamily: 'monospace',
                       background: '#0d1117', border: '1px solid #30363d', borderRadius: 4,
                       color: '#c9d1d9', padding: 8, resize: 'vertical', boxSizing: 'border-box' }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 6, alignItems: 'center' }}>
              <input type="file" accept=".csv,.txt" style={{ fontSize: 12, color: '#8b949e' }}
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (!file) return
                  const reader = new FileReader()
                  reader.onload = (ev) => setCsvText(ev.target?.result as string ?? '')
                  reader.readAsText(file)
                }}
              />
              <button className="primary" onClick={importCsv} disabled={importing || !csvText.trim()}>
                {importing ? 'Importing…' : 'Import'}
              </button>
              <button onClick={() => { setShowCsvForm(false); setCsvText('') }}>Cancel</button>
            </div>
            {csvError && <p style={{ color: '#f85149', fontSize: 12, marginTop: 6 }}>{csvError}</p>}
          </div>
        )}


        {true && (
          <div className="controls" style={{ marginBottom: 10 }}>
            <span className="muted" style={{ fontSize: 13 }}>Look back</span>
            <select value={congressDays} onChange={(e) => setCongressDays(Number(e.target.value))}
              style={{ fontSize: 13 }}>
              {[30, 60, 90, 180, 365].map((d) => <option key={d} value={d}>{d}d</option>)}
            </select>
            <span className="muted" style={{ fontSize: 13 }}>Min buys</span>
            <select value={congressMinBuys} onChange={(e) => setCongressMinBuys(Number(e.target.value))}
              style={{ fontSize: 13 }}>
              {[1, 2, 3, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <select value={congressChamber} onChange={(e) => setCongressChamber(e.target.value)}
              style={{ fontSize: 13 }}>
              <option value="both">Both chambers</option>
              <option value="house">House only</option>
              <option value="senate">Senate only</option>
            </select>
            <button onClick={() => loadCongress(false)} disabled={congressLoading}>
              {congressLoading ? 'Loading…' : congress ? 'Refresh' : 'Load politician trades'}
            </button>
            {congress && (
              <>
                <button onClick={() => loadCongress(true)} disabled={congressLoading}
                  style={{ opacity: 0.7 }} title="Force re-fetch raw data">
                  Force refresh
                </button>
                <button onClick={() => setTickerInput(congress.universe.join(', '))}>
                  Load into scanner
                </button>
                <button className="primary" onClick={async () => {
                  setTickerInput(congress.universe.join(', '))
                  setLoading(true); setError(null); setResults([])
                  try {
                    const r = await api.scanTickers(congress.universe)
                    setResults(r.results)
                  } catch (e) { setError((e as Error).message) }
                  finally { setLoading(false) }
                }} disabled={congressLoading || congress.universe.length === 0}>
                  Load + Scan
                </button>
                <button onClick={() => api.addToScanWatchlist(congress.universe).then(loadTracker)}>
                  + Track all
                </button>
              </>
            )}
            <button onClick={() => setShowCsvForm(v => !v)}
              style={{ fontSize: 12, opacity: 0.6 }} title="Manually import a CSV instead of scraping">
              {congressHasImport ? 'Replace CSV' : 'Import CSV'}
            </button>
            {congressHasImport && (
              <button onClick={clearImport} style={{ fontSize: 12, color: '#f85149', opacity: 0.7 }} title="Clear imported CSV">
                Clear CSV
              </button>
            )}
          </div>
        )}

        {congress && (
          <>
            {congress.tickers.length === 0 ? (
              <p className="muted" style={{ fontSize: 13 }}>
                No tickers found with {congressMinBuys}+ buys in the last {congressDays} days.
                Try lowering Min buys or widening the look-back window.
              </p>
            ) : (
              <>
                <table className="data" style={{ marginBottom: 8 }}>
                  <thead>
                    <tr>
                      <CSortTh col="ticker" label="Ticker" />
                      <CSortTh col="opportunity" label="Opportunity" />
                      <CSortTh col="txToDisclosure" label="Tx → Disclosed" title="Price move from transaction date to disclosure date — happened before you could act" />
                      <CSortTh col="sinceDisclosed" label="Since disclosed" title="Price move from disclosure date to today — what's left to capture" />
                      <CSortTh col="totalMove" label="Total (tx→now)" title="Total move from transaction date to today" />
                      <CSortTh col="politicians" label="Politicians" />
                      <CSortTh col="buys" label="Buys" />
                      <CSortTh col="score" label="Score" title="Technical signal score: −5 (full bear) to +5 (full bull). Same formula as the screener." />
                      <th title="Average win rate of politicians who bought this ticker">Investor quality</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedCongressTickers.map((t: CongressTicker) => {
                      const opp = t.opportunity ?? 'unknown'
                      const oppColor = opp === 'available' ? '#3fb950'
                        : opp === 'pullback' ? '#58a6ff'
                        : opp === 'fading'   ? '#d29922'
                        : opp === 'gone'     ? '#f85149'
                        : '#8b949e'
                      const oppLabel = opp === 'available' ? 'Available'
                        : opp === 'pullback'  ? 'Pulled back'
                        : opp === 'fading'    ? 'Fading'
                        : opp === 'gone'      ? 'Gone'
                        : '—'
                      const fmtPctCell = (v: number | null | undefined) => {
                        if (v == null) return <span className="muted">—</span>
                        const color = v >= 20 ? '#f85149' : v >= 10 ? '#d29922' : v >= 0 ? '#3fb950' : '#58a6ff'
                        return <span style={{ color, fontWeight: Math.abs(v) >= 10 ? 600 : undefined }}>
                          {v > 0 ? '+' : ''}{v.toFixed(1)}%
                        </span>
                      }
                      return (
                        <Fragment key={t.ticker}>
                          <tr style={{ cursor: 'pointer' }}
                            onClick={() => setCongressExpanded(congressExpanded === t.ticker ? null : t.ticker)}>
                            <td>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                                <span style={{ color: '#58a6ff', cursor: 'pointer' }}
                                  onClick={(e) => { e.stopPropagation(); navigate(`/ticker?sym=${t.ticker}`) }}>
                                  <strong>{t.ticker}</strong>
                                </span>
                                {t.priceNow != null && (
                                  <span className="muted" style={{ fontSize: 11 }}>
                                    ${t.priceNow.toFixed(2)}
                                  </span>
                                )}
                                {t.contrarian && (
                                  <span title="Smart money buying a technically weak ticker — contrarian signal"
                                    style={{ fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 600,
                                             background: 'rgba(210,153,34,0.15)', color: '#e3b341',
                                             border: '1px solid #e3b34155', whiteSpace: 'nowrap' }}>
                                    Smart Buy
                                  </span>
                                )}
                              </div>
                              {t.company && (
                                <div className="muted" style={{ fontSize: 11, marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 160 }}>
                                  {t.company}
                                </div>
                              )}
                            </td>
                            <td>
                              <span style={{ color: oppColor, fontWeight: 600, fontSize: 12 }}>
                                {oppLabel}
                              </span>
                            </td>
                            <td title={`Tx: $${t.priceAtTx ?? '?'} → Disclosed: $${t.priceAtDisclosure ?? '?'}`}>
                              {fmtPctCell(t.txToDisclosurePct)}
                            </td>
                            <td title={`Disclosed: $${t.priceAtDisclosure ?? '?'} → Now: $${t.priceNow ?? '?'}`}>
                              {fmtPctCell(t.disclosureToNowPct)}
                            </td>
                            <td title={`Tx: $${t.priceAtTx ?? '?'} → Now: $${t.priceNow ?? '?'}`}>
                              {fmtPctCell(t.totalMovePct)}
                            </td>
                            <td style={{ fontSize: 12, maxWidth: 220 }}>
                              {t.politicians.map((pol, pi) => (
                                <span key={pol}>
                                  <span
                                    style={{ color: '#58a6ff', cursor: 'pointer' }}
                                    onClick={(e) => { e.stopPropagation(); setPoliticianModal(pol) }}
                                  >
                                    {pol}
                                  </span>
                                  {pi < t.politicians.length - 1 && <span className="muted">, </span>}
                                </span>
                              ))}
                            </td>
                            <td className="muted">{t.buyCount}</td>
                            <td>
                              {t.score != null ? (
                                <strong style={{ color: scoreColor(t.score) }}>
                                  {t.score > 0 ? `+${t.score}` : t.score}
                                </strong>
                              ) : <span className="muted">—</span>}
                            </td>
                            <td onClick={(e) => e.stopPropagation()}>
                              {(() => {
                                const q = t.investorQuality ?? 'unknown'
                                const score = t.investorQualityScore
                                const [color, bg, label] =
                                  q === 'sharp'   ? ['#3fb950', 'rgba(63,185,80,0.12)',   'Sharp']   :
                                  q === 'mixed'   ? ['#d29922', 'rgba(210,153,34,0.12)',  'Mixed']   :
                                  q === 'weak'    ? ['#f85149', 'rgba(248,81,73,0.12)',   'Weak']    :
                                                    ['#8b949e', 'rgba(139,148,158,0.1)', '—']
                                return (
                                  <span title={score != null ? `Avg politician win rate: ${score}%` : undefined}
                                    style={{ color, background: bg, border: `1px solid ${color}33`,
                                             borderRadius: 4, padding: '1px 6px', fontSize: 11, fontWeight: 600 }}>
                                    {label}{score != null && q !== 'unknown' ? ` ${score}%` : ''}
                                  </span>
                                )
                              })()}
                            </td>
                            <td>
                              <span className="muted" style={{ fontSize: 11 }}>
                                {congressExpanded === t.ticker ? '▴' : '▾'}
                              </span>
                            </td>
                          </tr>
                          {congressExpanded === t.ticker && (
                            <tr>
                              <td colSpan={10} style={{ background: '#0d1117', padding: '8px 12px' }}>
                                {t.enrichError && (
                                  <p style={{ color: '#f85149', fontSize: 12, marginBottom: 6 }}>
                                    Price data unavailable: {t.enrichError}
                                  </p>
                                )}
                                {t.company && (
                                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{t.company}</div>
                                )}
                                <div style={{ fontSize: 12, marginBottom: 8, display: 'flex', gap: 24 }}>
                                  {t.priceAtTx != null && <span><span className="muted">Price at tx: </span>${t.priceAtTx.toFixed(2)}</span>}
                                  {t.priceAtDisclosure != null && <span><span className="muted">Price at disclosure: </span>${t.priceAtDisclosure.toFixed(2)}</span>}
                                  {t.priceNow != null && <span><span className="muted">Current: </span>${t.priceNow.toFixed(2)}</span>}
                                  {t.refTxDate && <span className="muted">Ref tx date: {t.refTxDate}</span>}
                                  {t.refDisclosedDate && <span className="muted">Ref disclosed: {t.refDisclosedDate}</span>}
                                </div>
                                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                                  <thead>
                                    <tr>
                                      <th style={{ textAlign: 'left', color: '#8b949e', paddingBottom: 4 }}>Politician</th>
                                      <th style={{ textAlign: 'left', color: '#8b949e', paddingBottom: 4 }}>Chamber</th>
                                      <th style={{ textAlign: 'left', color: '#8b949e', paddingBottom: 4 }}>Tx date</th>
                                      <th style={{ textAlign: 'left', color: '#8b949e', paddingBottom: 4 }}>Disclosed</th>
                                      <th style={{ textAlign: 'left', color: '#8b949e', paddingBottom: 4 }}>Amount</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {t.trades.map((tr, i) => (
                                      <tr key={i}>
                                        <td style={{ paddingBottom: 3 }}>
                                          <span
                                            style={{ color: '#58a6ff', cursor: 'pointer' }}
                                            onClick={() => setPoliticianModal(tr.politician)}
                                          >
                                            {tr.politician}
                                          </span>
                                        </td>
                                        <td className="muted" style={{ paddingBottom: 3, textTransform: 'capitalize' }}>{tr.chamber}</td>
                                        <td className="muted" style={{ paddingBottom: 3 }}>{tr.txDate || '—'}</td>
                                        <td className="muted" style={{ paddingBottom: 3 }}>{tr.disclosedDate || '—'}</td>
                                        <td className="muted" style={{ paddingBottom: 3 }}>{tr.amount || '—'}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>

                <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
                  <span className="muted" style={{ fontSize: 11 }}>
                    {congress.tickers.length} tickers · {congress.cacheInfo.totalTrades} trades cached
                    {congress.cacheInfo.source === 'csv' && ' · source: imported CSV'}
                    {congress.cacheInfo.source === 'scrape' && ' · source: capitoltrades.com'}
                    {congress.cacheInfo.source === 'api' && ' · source: Quiver API'}
                    {congress.cacheInfo.fetchedAt > 0 && ` · loaded ${fmtTime(congress.cacheInfo.fetchedAt)}`}
                    {congress.cacheInfo.errors.length > 0 && (
                      <span style={{ color: '#f85149' }}> · {congress.cacheInfo.errors.join('; ')}</span>
                    )}
                  </span>
                </div>

                {congress.universe.length > 0 && (
                  <div style={{
                    marginTop: 10, padding: '8px 12px',
                    background: '#161b22', borderRadius: 6,
                    border: '1px solid #21262d', fontSize: 12,
                  }}>
                    <span className="muted">Universe ({congress.universe.length}): </span>
                    {congress.universe.map((t, i) => (
                      <span key={t}>
                        <span style={{ cursor: 'pointer', color: '#58a6ff' }}
                          onClick={() => navigate(`/ticker?sym=${t}`)}>
                          {t}
                        </span>
                        {i < congress.universe.length - 1 && <span className="muted"> · </span>}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}
          </>
        )}

        {!congress && !congressLoading && (
          <p className="muted" style={{ fontSize: 13 }}>
            Click "Load politician trades" to scrape recent STOCK Act purchase disclosures from capitoltrades.com (free, no account needed). Takes ~5–15 seconds.
          </p>
        )}

        {/* ── Politician detail modal ── */}
        {politicianModal && congress && (() => {
          const polTrades = congress.tickers.flatMap(tk =>
            tk.trades
              .filter(tr => tr.politician === politicianModal)
              .map(tr => ({ ...tr, ticker: tk.ticker, txType: 'buy' as const }))
          ).sort((a, b) => (b.txDate || '').localeCompare(a.txDate || ''))
          const polSells = (congress.recentSells ?? [])
            .filter(s => s.politician === politicianModal)
          const stats = congress.politicianStats?.[politicianModal]
          const hasSellData = stats && stats.realizedCount > 0

          return (
            <div
              style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 200,
                       display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              onClick={() => setPoliticianModal(null)}
            >
              <div
                style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8,
                         padding: 24, width: 640, maxWidth: '95vw', maxHeight: '80vh',
                         overflow: 'auto' }}
                onClick={e => e.stopPropagation()}
              >
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
                  <div>
                    <h3 style={{ margin: 0 }}>{politicianModal}</h3>
                    {polTrades[0] && (
                      <span className="muted" style={{ fontSize: 12, textTransform: 'capitalize' }}>
                        {polTrades[0].chamber} · {polTrades[0].party}
                      </span>
                    )}
                  </div>
                  <button onClick={() => setPoliticianModal(null)} style={{ fontSize: 18, lineHeight: 1, padding: '2px 8px' }}>×</button>
                </div>

                {stats && (
                  <>
                    <div style={{ display: 'flex', gap: 12, marginBottom: 10, flexWrap: 'wrap' }}>
                      {(() => {
                        // Prefer realized win rate when sell data is available
                        const displayWr  = (hasSellData && stats.realizedWinRate != null) ? stats.realizedWinRate : stats.winRate
                        const wrLabel    = hasSellData && stats.realizedWinRate != null ? 'Realized win rate' : 'Win rate (est.)'
                        const wrColor    = displayWr >= 65 ? '#3fb950' : displayWr >= 45 ? '#d29922' : '#f85149'
                        const annGain    = stats.avgAnnualizedGain
                        const gainToShow = hasSellData ? stats.avgRealizedGain : annGain
                        const gainLabel  = hasSellData ? 'Avg realized gain' : 'Avg annualized (est.)'
                        const gainColor  = gainToShow == null ? '#8b949e' : gainToShow >= 0 ? '#3fb950' : '#f85149'
                        return (
                          <>
                            <div style={{ flex: 1, minWidth: 100, background: '#0d1117', borderRadius: 6,
                                          border: '1px solid #21262d', padding: '10px 14px', textAlign: 'center' }}>
                              <div style={{ fontSize: 22, fontWeight: 700, color: wrColor }}>{displayWr.toFixed(0)}%</div>
                              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{wrLabel}</div>
                              {hasSellData && stats.realizedWinRate != null && (
                                <div className="muted" style={{ fontSize: 10, marginTop: 1 }}>
                                  {stats.realizedWins}W / {stats.realizedLosses}L closed
                                </div>
                              )}
                            </div>
                            <div style={{ flex: 1, minWidth: 100, background: '#0d1117', borderRadius: 6,
                                          border: '1px solid #21262d', padding: '10px 14px', textAlign: 'center' }}>
                              <div style={{ fontSize: 22, fontWeight: 700, color: gainColor }}>
                                {gainToShow == null ? '—' : (gainToShow > 0 ? '+' : '') + gainToShow.toFixed(1) + '%'}
                              </div>
                              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>{gainLabel}</div>
                              {hasSellData && annGain != null && (
                                <div className="muted" style={{ fontSize: 10, marginTop: 1 }}>
                                  {annGain > 0 ? '+' : ''}{annGain.toFixed(1)}% annualized all
                                </div>
                              )}
                            </div>
                            <div style={{ flex: 1, minWidth: 100, background: '#0d1117', borderRadius: 6,
                                          border: '1px solid #21262d', padding: '10px 14px', textAlign: 'center' }}>
                              <div style={{ fontSize: 22, fontWeight: 700 }}>
                                {hasSellData ? `${stats.realizedCount}` : `${stats.wins}/${stats.trades}`}
                              </div>
                              <div className="muted" style={{ fontSize: 11, marginTop: 2 }}>
                                {hasSellData ? 'Closed trades' : 'Scored trades'}
                              </div>
                              {hasSellData && (
                                <div className="muted" style={{ fontSize: 10, marginTop: 1 }}>
                                  {stats.openCount} open (unrealized)
                                </div>
                              )}
                            </div>
                          </>
                        )
                      })()}
                    </div>
                    <p className="muted" style={{ fontSize: 11, marginBottom: 12 }}>
                      {hasSellData
                        ? `Realized stats use actual sell prices for ${stats.realizedCount} closed position${stats.realizedCount !== 1 ? 's' : ''}. Open positions use today's price as an estimate.`
                        : 'No sell disclosures found in the loaded window — all returns estimated using today\'s price as exit. Annualized return excludes holds under 7 days.'}
                    </p>
                  </>
                )}

                {(() => {
                  // Merge buys (enriched with return data) and sells into a single timeline
                  type BuyRow = { txType: 'buy'; ticker: string; txDate: string; disclosedDate: string; amount: string; assetDescription: string; txToDisclosurePct?: number|null; totalReturnPct?: number|null; realized?: boolean; sellDate?: string|null; priceAtBuy?: number|null; priceAtExit?: number|null }
                  type SellRow = { txType: 'sell'; ticker: string; txDate: string; disclosedDate: string; amount: string; assetDescription: string }
                  type Row = BuyRow | SellRow

                  const fmtPctBadge = (v: number | null | undefined, title?: string) => {
                    if (v == null) return <span className="muted">—</span>
                    const color = v > 0 ? '#3fb950' : v < 0 ? '#f85149' : '#8b949e'
                    return <span style={{ color, fontWeight: 600 }} title={title}>
                      {v > 0 ? '+' : ''}{v.toFixed(1)}%
                    </span>
                  }

                  const allRows: Row[] = [
                    ...polTrades.map(tr => ({
                      txType: 'buy' as const,
                      ticker: tr.ticker, txDate: tr.txDate || '', disclosedDate: tr.disclosedDate || '',
                      amount: tr.amount || '', assetDescription: tr.assetDescription || '',
                      txToDisclosurePct: tr.txToDisclosurePct,
                      totalReturnPct: tr.totalReturnPct,
                      realized: tr.realized,
                      sellDate: tr.sellDate,
                      priceAtBuy: tr.priceAtBuy,
                      priceAtExit: tr.priceAtExit,
                    })),
                    ...polSells.map(s => ({
                      txType: 'sell' as const,
                      ticker: s.ticker, txDate: s.txDate || '', disclosedDate: s.disclosedDate || '',
                      amount: s.amount || '', assetDescription: s.assetDescription || '',
                    })),
                  ].sort((a, b) => b.txDate.localeCompare(a.txDate))

                  if (allRows.length === 0) {
                    return <p className="muted" style={{ fontSize: 13 }}>No trades found for this politician in the loaded data.</p>
                  }

                  return (
                    <table className="data" style={{ width: '100%' }}>
                      <thead>
                        <tr>
                          <th style={{ textAlign: 'left' }}>Type</th>
                          <th style={{ textAlign: 'left' }}>Ticker</th>
                          <th style={{ textAlign: 'left' }}>Tx date</th>
                          <th style={{ textAlign: 'left' }}>Disclosed</th>
                          <th style={{ textAlign: 'left' }} title="Price move from transaction date to disclosure date">Tx→Disclosed</th>
                          <th style={{ textAlign: 'left' }} title="Total return from transaction date to current price (open) or actual sell price (closed)">Total return</th>
                          <th style={{ textAlign: 'left' }}>Amount</th>
                        </tr>
                      </thead>
                      <tbody>
                        {allRows.map((row, i) => (
                          <tr key={i}>
                            <td>
                              <span style={{
                                fontSize: 10, padding: '1px 5px', borderRadius: 3, fontWeight: 600,
                                background: row.txType === 'buy' ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)',
                                color: row.txType === 'buy' ? '#3fb950' : '#f85149',
                                border: `1px solid ${row.txType === 'buy' ? '#3fb95033' : '#f8514933'}`,
                              }}>
                                {row.txType === 'buy' ? 'Buy' : 'Sell'}
                              </span>
                            </td>
                            <td>
                              <span style={{ color: '#58a6ff', cursor: 'pointer', fontWeight: 600 }}
                                onClick={() => navigate(`/ticker?sym=${row.ticker}`)}>
                                {row.ticker}
                              </span>
                              {row.assetDescription && (
                                <div className="muted" style={{ fontSize: 11, marginTop: 1 }}>{row.assetDescription}</div>
                              )}
                            </td>
                            <td className="muted" style={{ fontSize: 12 }}>{row.txDate || '—'}</td>
                            <td className="muted" style={{ fontSize: 12 }}>{row.disclosedDate || '—'}</td>
                            <td style={{ fontSize: 12 }}>
                              {row.txType === 'buy'
                                ? fmtPctBadge(row.txToDisclosurePct, `$${row.priceAtBuy ?? '?'} → $${(row as BuyRow).priceAtExit ?? '?'} at disclosure`)
                                : <span className="muted">—</span>}
                            </td>
                            <td style={{ fontSize: 12 }}>
                              {row.txType === 'buy' ? (() => {
                                const br = row as BuyRow
                                const badge = fmtPctBadge(br.totalReturnPct, `$${br.priceAtBuy ?? '?'} → $${br.priceAtExit ?? '?'}`)
                                return (
                                  <span>
                                    {badge}
                                    {br.realized
                                      ? <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>(closed {br.sellDate})</span>
                                      : <span className="muted" style={{ fontSize: 10, marginLeft: 4 }}>(open)</span>}
                                  </span>
                                )
                              })() : <span className="muted">—</span>}
                            </td>
                            <td className="muted" style={{ fontSize: 12 }}>{row.amount || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )
                })()}

                <div style={{ marginTop: 12, fontSize: 12, color: '#8b949e' }}>
                  {polTrades.length} buy{polTrades.length !== 1 ? 's' : ''} · {polSells.length} sell{polSells.length !== 1 ? 's' : ''} in loaded window ·{' '}
                  <span className="muted">buys limited to tickers with {congressMinBuys}+ buys in last {congressDays}d</span>
                </div>
              </div>
            </div>
          )
        })()}
      </div>

      {/* ── auto-trade engine ─────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Auto-Trade Engine
          <Tip text="Runs automatically at 10:15 AM and 4:15 PM ET. Scans all three signal sources (technical score ≥ 3, sustained streak, Smart Buy alerts), ranks candidates by composite score, sizes each position via ATR-based 1% equity risk, and creates paper trading instances that execute through the normal engine flow." />
        </h2>
        <p style={{ fontSize: 12, color: '#8b949e', margin: '0 0 12px' }}>
          Automatically selects stocks, sizes positions (1% equity risk / ATR), and executes in the paper engine. No manual picks required.
        </p>

        {!autoSelectStatus && (
          <div className="controls">
            <button onClick={loadAutoSelectStatus} disabled={autoSelectLoading}>
              {autoSelectLoading ? 'Loading…' : 'Load Auto-Trade Status'}
            </button>
            <button onClick={runAutoSelectNow} disabled={autoSelectRunning}
              style={{ background: 'rgba(63,185,80,0.15)', color: '#3fb950', borderColor: '#3fb950' }}>
              {autoSelectRunning ? 'Running…' : 'Run Now'}
            </button>
          </div>
        )}

        {autoSelectError && <div className="error-banner" style={{ marginTop: 6 }}>{autoSelectError}</div>}

        {autoSelectStatus && (() => {
          const res: AutoSelectResult | null = autoSelectStatus.lastResult
          const jobs = autoSelectStatus.scheduledJobs
          const nextJob = jobs.find((j) => j.nextRun)

          const SOURCE_LABELS: Record<string, string> = {
            technical:     'Technical',
            sustained:     'Sustained',
            smart_buy:     'Smart Buy',
            smart_universe:'Smart Universe',
            congress:      'Politician Trades',
          }
          const SOURCE_COLORS: Record<string, string> = {
            technical:     '#58a6ff',
            sustained:     '#3fb950',
            smart_buy:     '#e3b341',
            smart_universe:'#d2a8ff',
            congress:      '#f0883e',
          }
          const STRATEGY_LABELS: Record<string, string> = {
            rsi_revert:     'RSI mean reversion',
            ema_cross_9_21: 'EMA cross (9/21)',
            supertrend:     'Supertrend',
          }

          return (
            <>
              <div className="controls" style={{ marginBottom: 12 }}>
                <button onClick={loadAutoSelectStatus} disabled={autoSelectLoading} style={{ fontSize: 12 }}>
                  {autoSelectLoading ? 'Refreshing…' : 'Refresh'}
                </button>
                <button onClick={runAutoSelectNow} disabled={autoSelectRunning}
                  style={{ fontSize: 12, background: 'rgba(63,185,80,0.15)', color: '#3fb950', borderColor: '#3fb950' }}>
                  {autoSelectRunning ? 'Running…' : 'Run Now'}
                </button>
                {nextJob && (
                  <span className="muted" style={{ fontSize: 12 }}>
                    Next auto-run: {new Date(nextJob.nextRun!).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                  </span>
                )}
              </div>

              {!res && (
                <p className="muted" style={{ fontSize: 13 }}>
                  No selection runs yet. Click "Run Now" to trigger the first scan.
                </p>
              )}

              {res && (
                <div>
                  {/* Summary row */}
                  <div style={{ display: 'flex', gap: 24, marginBottom: 16, flexWrap: 'wrap' }}>
                    {[
                      { label: 'Last run', value: res.ts ? fmtTime(res.ts) : '—' },
                      { label: 'Candidates found', value: String(res.candidates ?? '—') },
                      { label: 'Selected', value: String(res.selected), color: res.selected > 0 ? '#3fb950' : '#8b949e' },
                      { label: 'Open positions', value: String(res.openPositions ?? '—') },
                      { label: 'Paper equity', value: res.equity ? `$${res.equity.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—' },
                      { label: 'Available cash', value: res.cash ? `$${res.cash.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—', color: (res.cash && res.equity && res.cash / res.equity < 0.15) ? '#e3b341' : undefined },
                    ].map((s) => (
                      <div key={s.label}>
                        <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 2 }}>{s.label}</div>
                        <div style={{ fontSize: 16, fontWeight: 700, color: s.color ?? '#e6edf3' }}>{s.value}</div>
                      </div>
                    ))}
                  </div>

                  {res.skipped === 'low_cash' && (
                    <div className="stale-banner" style={{ marginBottom: 10 }}>
                      Skipped — available cash is below the 10% floor. Free up capital or reduce open positions before the next run.
                    </div>
                  )}

                  {res.errors && res.errors.length > 0 && (
                    <div className="error-banner" style={{ marginBottom: 10 }}>
                      {res.errors.map((e, i) => <div key={i}>{e}</div>)}
                    </div>
                  )}

                  {res.selections && res.selections.length > 0 && (
                    <>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: '#3fb950' }}>
                        {res.selections.length} new position{res.selections.length !== 1 ? 's' : ''} opened this run
                      </div>
                      <table className="data">
                        <thead>
                          <tr>
                            <th>Symbol</th>
                            <th>Signal sources</th>
                            <th>Strategy</th>
                            <th style={{ textAlign: 'right' }}>Allocation</th>
                            <th style={{ textAlign: 'right' }}>Score</th>
                            <th style={{ textAlign: 'right' }}>Price</th>
                            <th style={{ textAlign: 'right' }}>ATR</th>
                          </tr>
                        </thead>
                        <tbody>
                          {res.selections.map((s) => (
                            <tr key={s.symbol} style={{ cursor: 'pointer' }}
                              onClick={() => navigate(`/ticker?sym=${s.symbol}`)}>
                              <td style={{ fontWeight: 600 }}>
                                {s.symbol}
                                <NameSub sym={s.symbol} />
                                {s.smartBuy && (
                                  <span style={{ marginLeft: 6, fontSize: 11, padding: '1px 6px', borderRadius: 4,
                                    background: 'rgba(227,179,65,0.15)', color: '#e3b341' }}>
                                    Smart Buy
                                  </span>
                                )}
                              </td>
                              <td>
                                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                                  {s.sources.map((src) => (
                                    <span key={src} style={{
                                      fontSize: 11, padding: '1px 6px', borderRadius: 4,
                                      background: (SOURCE_COLORS[src] ?? '#8b949e') + '22',
                                      color: SOURCE_COLORS[src] ?? '#8b949e',
                                    }}>
                                      {SOURCE_LABELS[src] ?? src}
                                    </span>
                                  ))}
                                </div>
                              </td>
                              <td style={{ fontSize: 12, color: '#8b949e' }}>
                                {STRATEGY_LABELS[s.strategy] ?? s.strategy}
                              </td>
                              <td style={{ textAlign: 'right', fontWeight: 600 }}>
                                ${s.allocationUsd.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </td>
                              <td style={{ textAlign: 'right', color: '#3fb950', fontWeight: 600 }}>
                                {s.compositeScore.toFixed(1)}
                              </td>
                              <td style={{ textAlign: 'right' }}>${s.price.toFixed(2)}</td>
                              <td style={{ textAlign: 'right', color: '#8b949e' }}>{s.atr.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}

                  {res.selected === 0 && !res.skipped && (
                    <p className="muted" style={{ fontSize: 13 }}>
                      {res.candidates === 0
                          ? 'No candidates found. Add symbols to the Signal Tracker or load the Congress Universe to build scan history.'
                          : 'Candidates found but all are already in open positions or existing instances.'}
                    </p>
                  )}
                </div>
              )}
            </>
          )
        })()}
      </div>

      {/* ── smart buy monitor ─────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Smart Buy Monitor
          <Tip text="Tickers where the technical score is weak (≤ −1) but high-quality politicians (≥ 65% win rate) are actively buying. Detected automatically at 10:10 AM and 4:10 PM ET. The assessment updates as score history accumulates — 'Confirmed' means technicals now agree with smart money." />
        </h2>
        <p style={{ fontSize: 12, color: '#8b949e', margin: '0 0 12px' }}>
          Contrarian setups: score low, smart politicians buying. Score history tracked automatically once detected.
        </p>

        {!smartBuysLoaded && (
          <button onClick={loadSmartBuys} disabled={smartBuysLoading}>
            {smartBuysLoading ? 'Loading…' : 'Load Smart Buy Monitor'}
          </button>
        )}
        {smartBuysError && <div className="error-banner" style={{ marginTop: 6 }}>{smartBuysError}</div>}

        {smartBuysLoaded && (
          <>
            <div className="controls" style={{ marginBottom: 10 }}>
              <button onClick={loadSmartBuys} disabled={smartBuysLoading} style={{ fontSize: 12 }}>
                {smartBuysLoading ? 'Refreshing…' : 'Refresh'}
              </button>
              <span className="muted" style={{ fontSize: 12 }}>
                {smartBuys.length === 0
                  ? 'No detections yet — alerts populate automatically at 10:10 AM and 4:10 PM ET on trading days.'
                  : `${smartBuys.length} ticker${smartBuys.length !== 1 ? 's' : ''} detected in last 30 days`}
              </span>
            </div>

            {smartBuys.length > 0 && (() => {
              const ASSESSMENT_META: Record<SmartBuyAlert['assessment'], { label: string; color: string; tip: string }> = {
                new:          { label: 'New',          color: '#58a6ff', tip: 'Just detected. Smart money buying. Watch for entry.' },
                accumulate:   { label: 'Accumulate',   color: '#e3b341', tip: 'Score weak but stable. Consider accumulating on dips.' },
                improving:    { label: 'Improving',    color: '#d29922', tip: 'Score trending up. Thesis developing — watch for entry.' },
                confirmed:    { label: 'Confirmed',    color: '#3fb950', tip: 'Technicals now bullish. Smart money + technicals aligned.' },
                stale:        { label: 'Stale',        color: '#8b949e', tip: 'No technical improvement after 14+ days. Re-evaluate thesis.' },
                deteriorating:{ label: 'Deteriorating',color: '#f85149', tip: 'Score worsening since detection. Avoid until stabilization.' },
              }
              return (
                <table className="data">
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Detected</th>
                      <th style={{ textAlign: 'right' }}>Entry Score</th>
                      <th style={{ textAlign: 'right' }}>Current</th>
                      <th>Trend</th>
                      <th>Investor Quality</th>
                      <th>Buying Politicians</th>
                      <th>Assessment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {smartBuys.map((a) => {
                      const meta = ASSESSMENT_META[a.assessment]
                      const trendDelta = (a.currentScore ?? a.scoreAtDetection) - a.scoreAtDetection
                      const trendArrow = trendDelta > 0 ? '↑' : trendDelta < 0 ? '↓' : '→'
                      const trendCol = trendDelta > 0 ? '#3fb950' : trendDelta < 0 ? '#f85149' : '#8b949e'
                      return (
                        <tr key={a.ticker} style={{ cursor: 'pointer' }}
                          onClick={() => navigate(`/ticker?sym=${a.ticker}`)}>
                          <td>
                            <span style={{ fontWeight: 600 }}>{a.ticker}</span>
                            <NameSub sym={a.ticker} />
                          </td>
                          <td className="muted" style={{ fontSize: 12 }}>{fmtTime(a.detectedAt)}</td>
                          <td style={{ textAlign: 'right' }}>
                            <span style={{ color: scoreColor(a.scoreAtDetection), fontWeight: 600 }}>
                              {a.scoreAtDetection > 0 ? '+' : ''}{a.scoreAtDetection}
                            </span>
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            {a.currentScore != null
                              ? <span style={{ color: scoreColor(a.currentScore), fontWeight: 600 }}>
                                  {a.currentScore > 0 ? '+' : ''}{a.currentScore}
                                </span>
                              : <span className="muted">—</span>}
                          </td>
                          <td>
                            <span style={{ color: trendCol, fontWeight: 600, fontSize: 16 }}>{trendArrow}</span>
                            {trendDelta !== 0 && (
                              <span style={{ color: trendCol, fontSize: 11, marginLeft: 3 }}>
                                {trendDelta > 0 ? '+' : ''}{trendDelta}
                              </span>
                            )}
                          </td>
                          <td>
                            <span style={{ color: '#e3b341', fontWeight: 600 }}>
                              Sharp {a.investorQualityScore != null ? `(${a.investorQualityScore}%)` : ''}
                            </span>
                          </td>
                          <td style={{ fontSize: 12, color: '#8b949e', maxWidth: 200 }}>
                            {a.politicians.slice(0, 3).join(', ')}
                            {a.politicians.length > 3 && ` +${a.politicians.length - 3}`}
                          </td>
                          <td title={meta.tip}>
                            <span style={{
                              display: 'inline-block', padding: '2px 8px', borderRadius: 4,
                              fontSize: 12, fontWeight: 600, letterSpacing: '0.02em',
                              background: meta.color + '22', color: meta.color,
                            }}>
                              {meta.label}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              )
            })()}
          </>
        )}
      </div>

      {/* ── signal tracker ────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Signal Tracker
          <Tip text="Tickers here are scanned automatically at 9:35 AM and 4:35 PM ET every trading day. Score history builds so you can surface tickers holding a bullish alignment for multiple consecutive scans — a sustained signal is far more reliable than a single reading." />
        </h2>

        <div style={{ marginBottom: 12 }}>
          <div className="controls" style={{ marginBottom: 6, flexWrap: 'wrap' }}>
            <span className="muted" style={{ fontSize: 12 }}>Tracking:</span>
            {trackedSymbols.length === 0 && (
              <span className="muted" style={{ fontSize: 12 }}>
                None yet — add symbols below, or use "Track all" from the Smart Universe.
              </span>
            )}
            {trackedSymbols.map((s) => (
              <span key={s.symbol} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
                <button style={{ fontSize: 12, padding: '2px 8px' }}
                  onClick={() => navigate(`/ticker?sym=${s.symbol}`)}>
                  {s.symbol}
                </button>
                <button style={{ fontSize: 11, padding: '2px 5px', opacity: 0.5 }}
                  onClick={() => removeFromTracker(s.symbol)}>✕</button>
              </span>
            ))}
          </div>
          <div className="controls">
            <input value={addTrackerInput} onChange={(e) => setAddTrackerInput(e.target.value)}
              placeholder="Add symbols: AAPL MSFT NVDA" style={{ width: 240, fontSize: 13 }}
              onKeyDown={(e) => { if (e.key === 'Enter') addToTracker() }} />
            <button onClick={addToTracker} disabled={!addTrackerInput.trim()}>Add</button>
            <button onClick={scanNow} disabled={scanningNow || trackedSymbols.length === 0}>
              {scanningNow ? 'Scanning…' : 'Scan now'}
            </button>
            {trackerLoading && <span className="muted" style={{ fontSize: 12 }}>Loading…</span>}
          </div>
          {trackerError && <div className="error-banner" style={{ marginTop: 6 }}>{trackerError}</div>}
        </div>

        <div style={{ borderTop: '1px solid #21262d', paddingTop: 12 }}>
          <div className="controls" style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 13 }}>Show tickers with score ≥</span>
            <select value={minScore} onChange={(e) => setMinScore(Number(e.target.value))}
              style={{ fontSize: 13, width: 60 }}>
              {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span style={{ fontSize: 13 }}>for at least</span>
            <select value={minStreak} onChange={(e) => setMinStreak(Number(e.target.value))}
              style={{ fontSize: 13, width: 70 }}>
              {[2, 3, 5, 7, 10].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span style={{ fontSize: 13 }}>consecutive scans</span>
          </div>

          {sustained.length === 0 && !trackerLoading && (
            <p className="muted" style={{ fontSize: 13 }}>
              {trackedSymbols.length === 0
                ? 'Add symbols to the tracker and click "Scan now" to start building history.'
                : `No tickers have held score ≥ ${minScore} for ${minStreak}+ consecutive scans yet.`}
            </p>
          )}

          {sustained.length > 0 && (
            <table className="data">
              <thead>
                <tr>
                  <SSortTh col="symbol" label="Symbol" />
                  <SSortTh col="streak" label="Streak" />
                  <SSortTh col="score" label="Score" />
                  <th>Trend</th>
                  <SSortTh col="rsi" label="RSI" />
                  <th>MACD</th>
                  <SSortTh col="price" label="Price" />
                  <th>Score history</th>
                  <th>Last scan</th>
                  <th>Deploy</th>
                </tr>
              </thead>
              <tbody>
                {sortedSustained.map((r) => (
                  <tr key={r.symbol}>
                    <td>
                      <strong style={{ cursor: 'pointer', color: '#58a6ff' }}
                        onClick={() => navigate(`/ticker?sym=${r.symbol}`)}>
                        {r.symbol}
                      </strong>
                      {r.stale ? <span className="muted" style={{ fontSize: 11 }}> *</span> : null}
                      <NameSub sym={r.symbol} />
                    </td>
                    <td>
                      <strong style={{
                        color: r.streak >= 10 ? '#3fb950' : r.streak >= 5 ? '#56d364' : '#e3b341',
                        fontSize: 15,
                      }}>{r.streak}</strong>
                      <span className="muted" style={{ fontSize: 11 }}> scans</span>
                    </td>
                    <td>
                      <strong style={{ color: scoreColor(r.score) }}>
                        {r.score > 0 ? `+${r.score}` : r.score}
                      </strong>
                    </td>
                    <td style={{ color: trendColor(r.trend) }}>{r.trend ?? '—'}</td>
                    <td className={r.rsi != null && r.rsi < 30 ? 'pos' : r.rsi != null && r.rsi > 70 ? 'neg' : ''}>
                      {r.rsi?.toFixed(0) ?? '—'}
                    </td>
                    <td className={r.macdAboveSignal === true ? 'pos' : r.macdAboveSignal === false ? 'neg' : ''}>
                      {r.macdAboveSignal === true ? '↑' : r.macdAboveSignal === false ? '↓' : '—'}
                    </td>
                    <td>{r.lastPrice?.toFixed(2) ?? '—'}</td>
                    <td><ScoreSparkline history={r.history} /></td>
                    <td className="muted" style={{ fontSize: 11 }}>
                      {r.lastSeen ? fmtTime(r.lastSeen) : '—'}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <DeployCell symbol={r.symbol} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {deployResult && (
          <div className="stale-banner" style={{ borderColor: '#3fb950', color: '#3fb950', background: 'rgba(63,185,80,0.08)', marginTop: 10 }}>
            Deployed {deployResult.symbol} → {deployResult.strategyLabel}
            {deployResult.sharpe != null ? ` (Sharpe ${deployResult.sharpe.toFixed(2)})` : ''},
            ${deployResult.allocationUsd.toLocaleString()} allocation. Instance #{deployResult.instanceId}.
            <button style={{ marginLeft: 12, fontSize: 12 }} onClick={() => setDeployResult(null)}>✕</button>
          </div>
        )}
        {deployError && (
          <div className="error-banner" style={{ marginTop: 10 }}>
            Deploy failed: {deployError}
            <button style={{ marginLeft: 12, fontSize: 12 }} onClick={() => setDeployError(null)}>✕</button>
          </div>
        )}
      </div>

      {/* ── ad-hoc scan ────────────────────────────────────────────────────── */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <h2>
          Ad-hoc Scan
          <Tip text="Scan any tickers once. Results are not stored. Use the Smart Universe above to auto-populate the list, then scan." />
        </h2>

        <div className="controls" style={{ marginBottom: 6 }}>
          {Object.keys(presets).map((k) => (
            <button key={k} onClick={() => setTickerInput(presets[k].join(', '))}>
              {PRESET_LABELS[k] ?? k}
            </button>
          ))}
        </div>

        {watchlists.length > 0 && (
          <div className="controls" style={{ marginBottom: 6, flexWrap: 'wrap' }}>
            <span className="muted" style={{ fontSize: 12 }}>Saved:</span>
            {watchlists.map((w) => (
              <span key={w.name} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <button style={{ fontSize: 12, padding: '2px 8px' }}
                  onClick={() => setTickerInput(w.tickers)}>{w.name}</button>
                <button style={{ fontSize: 11, padding: '2px 5px', opacity: 0.6 }}
                  onClick={() => deleteWatchlist(w.name)}>✕</button>
              </span>
            ))}
          </div>
        )}

        <textarea value={tickerInput} onChange={(e) => setTickerInput(e.target.value)}
          placeholder="AAPL, MSFT, NVDA, … or use Smart Universe above to auto-populate"
          rows={3}
          style={{ width: '100%', resize: 'vertical', fontFamily: 'monospace', fontSize: 13 }} />

        <div className="controls" style={{ marginTop: 8 }}>
          <button className="primary" onClick={() => runScan()} disabled={loading || !tickerInput.trim()}>
            {loading ? 'Scanning…' : 'Scan'}
          </button>
          {tickerInput.trim() && (
            showSaveInput ? (
              <>
                <input value={newListName} onChange={(e) => setNewListName(e.target.value)}
                  placeholder="List name" style={{ width: 120, fontSize: 13 }}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveCurrentList() }} />
                <button onClick={saveCurrentList} disabled={!newListName.trim()}>Save</button>
                <button onClick={() => { setShowSaveInput(false); setNewListName('') }}>Cancel</button>
              </>
            ) : (
              <button onClick={() => setShowSaveInput(true)} style={{ fontSize: 13 }}>+ Save list</button>
            )
          )}
          {results.length > 0 && (
            <span className="muted" style={{ fontSize: 13 }}>{results.length} tickers scanned</span>
          )}
        </div>
        {error && <div className="error-banner" style={{ marginTop: 8 }}>{error}</div>}
      </div>

      {results.length > 0 && (
        <>
          {/* ── analytics pipeline funnel ──────────────────────────────────── */}
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2>Analytics Pipeline</h2>
            <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', fontSize: 13, alignItems: 'center' }}>
              <div><span className="muted">Universe </span><strong>{funnelCounts.total}</strong></div>
              <div className="muted">→</div>
              <div>
                <span className="muted">Uptrend </span>
                <strong style={{ color: '#3fb950' }}>{funnelCounts.uptrend}</strong>
                <span className="muted"> ({funnelCounts.total > 0 ? Math.round(funnelCounts.uptrend / funnelCounts.total * 100) : 0}%)</span>
              </div>
              <div className="muted">→</div>
              <div>
                <span className="muted">MACD bull </span>
                <strong style={{ color: '#58a6ff' }}>{funnelCounts.macdBull}</strong>
                <span className="muted"> ({funnelCounts.total > 0 ? Math.round(funnelCounts.macdBull / funnelCounts.total * 100) : 0}%)</span>
              </div>
              <div className="muted">→</div>
              <div>
                <span className="muted">Score ≥ 2 </span>
                <strong style={{ color: '#56d364' }}>{funnelCounts.bullScore}</strong>
                <span className="muted"> ({funnelCounts.total > 0 ? Math.round(funnelCounts.bullScore / funnelCounts.total * 100) : 0}%)</span>
              </div>
            </div>
          </div>

          {/* ── signal results table ────────────────────────────────────────── */}
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2>
              Signal Results — {filtered.length} of {results.filter((r) => !r.error).length}
            </h2>

            <div className="controls" style={{ marginBottom: 10, flexWrap: 'wrap' }}>
              <span className="muted" style={{ fontSize: 12 }}>Filter:</span>
              {([
                ['uptrend', 'Uptrend'], ['downtrend', 'Downtrend'],
                ['oversold', 'RSI < 30'], ['overbought', 'RSI > 70'],
                ['macdBull', 'MACD ↑'], ['macdBear', 'MACD ↓'],
                ['withCross', 'Recent cross'],
              ] as [keyof Filters, string][]).map(([k, label]) => (
                <label key={k} style={{ fontSize: 13, color: filters[k] ? '#e6edf3' : '#8b949e', cursor: 'pointer' }}>
                  <input type="checkbox" checked={filters[k]}
                    onChange={() => setFilters((f) => ({ ...f, [k]: !f[k] }))} /> {label}
                </label>
              ))}
            </div>

            {filtered.length === 0 ? (
              <p className="muted">No tickers match the current filters.</p>
            ) : (
              <table className="data">
                <thead>
                  <tr>
                    <SortTh col="symbol" label="Symbol" />
                    <SortTh col="last" label="Last" />
                    <th>Trend</th>
                    <th>Cross</th>
                    <SortTh col="rsi" label="RSI" />
                    <th>MACD</th>
                    <th>BB</th>
                    <SortTh col="atrPct" label="ATR%" />
                    <SortTh col="score" label="Score" />
                    <th>Track</th>
                    <th>Deploy</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <Fragment key={r.symbol}>
                      <tr style={{ cursor: 'pointer' }}>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}>
                          <strong>{r.symbol}</strong>{r.stale ? ' *' : ''}
                          <NameSub sym={r.symbol} />
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}>
                          {r.last?.toFixed(2) ?? '—'}
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}
                          style={{ color: trendColor(r.trend) }}>{r.trend ?? '—'}</td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)} style={{ fontSize: 12 }}>
                          {r.recentCross === 'golden_cross' ? '☀ golden'
                            : r.recentCross === 'death_cross' ? '☽ death' : '—'}
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}
                          className={r.rsi != null && r.rsi < 30 ? 'pos' : r.rsi != null && r.rsi > 70 ? 'neg' : ''}>
                          {r.rsi?.toFixed(0) ?? '—'}
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}
                          className={r.macdAboveSignal === true ? 'pos' : r.macdAboveSignal === false ? 'neg' : ''}>
                          {r.macdAboveSignal === true ? '↑' : r.macdAboveSignal === false ? '↓' : '—'}
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}
                          className={r.bollingerStretch != null && Math.abs(r.bollingerStretch) > 1 ? 'warn' : ''}>
                          {r.bollingerStretch?.toFixed(2) ?? '—'}
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}>
                          {r.atrPct?.toFixed(1) ?? '—'}%
                        </td>
                        <td onClick={() => navigate(`/ticker?sym=${r.symbol}`)}>
                          <strong style={{ color: scoreColor(r.score) }}>
                            {r.score > 0 ? `+${r.score}` : r.score}
                          </strong>
                        </td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <button
                            style={{ fontSize: 11, padding: '2px 7px', opacity: isTracked(r.symbol) ? 0.4 : 1 }}
                            disabled={isTracked(r.symbol)}
                            onClick={() => trackFromResult(r.symbol)}
                            title={isTracked(r.symbol) ? 'Already tracked' : 'Add to Signal Tracker'}>
                            {isTracked(r.symbol) ? '✓' : '+ Track'}
                          </button>
                        </td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <DeployCell symbol={r.symbol} />
                        </td>
                      </tr>
                    </Fragment>
                  ))}
                </tbody>
              </table>
            )}
            {errors.length > 0 && (
              <div className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                Failed: {errors.map((e) => `${e.symbol} (${e.error})`).join(' · ')}
              </div>
            )}
          </div>

          {/* ── batch backtest grid ─────────────────────────────────────────── */}
          <div className="panel">
            <h2>
              Batch Backtest Grid
              <Tip text="All strategies vs all scanned tickers. Green = Sharpe >1, red = strategy lost money on this ticker over the period. Best column shows the top historical fit." />
            </h2>
            <div className="controls" style={{ marginBottom: 10 }}>
              <select value={batchPeriod} onChange={(e) => setBatchPeriod(e.target.value)}>
                {['1y', '2y', '3y', '5y'].map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              <button className="primary" onClick={runBatch}
                disabled={batchLoading || results.filter((r) => !r.error).length === 0}>
                {batchLoading
                  ? 'Running…'
                  : `Run batch (${results.filter((r) => !r.error).length} tickers)`}
              </button>
              {batchData && <span className="muted" style={{ fontSize: 13 }}>Cells show Sharpe ratio</span>}
            </div>
            {batchError && <div className="error-banner">{batchError}</div>}

            {batchData && (
              <table className="data">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    {batchData.strategies.map((s) => (
                      <th key={s} style={{ fontSize: 12 }}>{batchData.strategyLabels[s] ?? s}</th>
                    ))}
                    <th>Best</th>
                  </tr>
                </thead>
                <tbody>
                  {batchData.tickers.map((sym) => (
                    <tr key={sym}>
                      <td>
                        <span style={{ cursor: 'pointer', color: '#58a6ff' }}
                          onClick={() => navigate(`/ticker?sym=${sym}`)}>
                          <strong>{sym}</strong>
                        </span>
                        <NameSub sym={sym} />
                      </td>
                      {batchData.strategies.map((s) => {
                        const cell = batchGrid[sym]?.[s]
                        const isBest = bestStrategy[sym] === s
                        return (
                          <td key={s} style={{
                            color: cell?.error ? '#8b949e' : sharpeColor(cell?.sharpe),
                            fontWeight: isBest ? 700 : undefined,
                            background: isBest ? 'rgba(63,185,80,0.08)' : undefined,
                            fontSize: 13,
                          }}>
                            {cell?.error ? 'err' : cell?.sharpe != null ? cell.sharpe.toFixed(2) : '—'}
                          </td>
                        )
                      })}
                      <td style={{ fontSize: 12 }} className="muted">
                        {bestStrategy[sym] ? batchData.strategyLabels[bestStrategy[sym]] : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}

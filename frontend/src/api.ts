export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface Point {
  time: number
  value: number
}

export interface Level {
  price: number
  touches: number
  kind: 'support' | 'resistance'
}

export interface IndicatorBundle {
  symbol: string
  stale: boolean
  sma20: Point[]
  sma50: Point[]
  sma200: Point[]
  ema9: Point[]
  ema21: Point[]
  rsi: Point[]
  rsiZ: Point[]
  macd: Point[]
  macdSignal: Point[]
  macdHist: Point[]
  bbUpper: Point[]
  bbLower: Point[]
  atr: Point[]
  obv: Point[]
  levels: Level[]
}

export interface Signals {
  symbol: string
  stale: boolean
  last: number
  trend: string
  recentCross: string | null
  rsi: number | null
  momentum: string
  macdAboveSignal: boolean | null
  bollingerStretch: number | null
  atrPct: number | null
  sma50: number | null
  sma200: number | null
  levels: Level[]
}

export interface MacroAsset {
  symbol: string
  name: string
  group: string
  last: number
  changePct: number
  ret20Pct: number
  spark: number[]
  stale: boolean
}

export interface MacroDispersion {
  vix: number
  vixeq: number
  spread: number
  impliedCorrelation: number
  stale: boolean
}

export interface MacroSnapshot {
  assets: MacroAsset[]
  curve10y3m: number | null
  riskComposite: number | null
  dispersion: MacroDispersion | null
  stale: boolean
}

export interface GexStrike {
  strike: number
  callGex: number
  putGex: number
  netGex: number
  callOi: number
  putOi: number
}

export interface GexData {
  symbol: string
  spot: number
  expiration: string
  expirations: string[]
  strikes: GexStrike[]
  netGexTotal: number
  flipPoint: number | null
  maxPain: number | null
  putCallOiRatio: number | null
  keyLevels: number[]
  stale: boolean
}

export interface RelValData {
  a: string
  b: string
  window: number
  ratio: Point[]
  zscore: Point[]
  correlation: Point[]
  current: {
    ratio: number
    zscore: number | null
    correlation: number | null
    halfLifeDays: number | null
    read: string
  }
  stale: boolean
}

export interface BacktestResult {
  symbol: string
  strategy: string
  label: string
  params: Record<string, number>
  period: string
  metrics: {
    totalReturnPct: number
    buyHoldReturnPct: number
    cagrPct: number
    sharpe: number | null
    maxDrawdownPct: number
    buyHoldMaxDrawdownPct: number
    trades: number
    winRatePct: number | null
    timeInMarketPct: number
  }
  equity: Point[]
  buyHold: Point[]
  stale: boolean
}

export interface StrategySpec {
  id: string
  label: string
  defaultParams: Record<string, number>
}

export interface PaperPosition {
  symbol: string
  qty: number
  avgCost: number
  last: number | null
  value: number
  unrealizedPnl: number
}

export interface PaperPortfolio {
  equity: number
  cash: number
  positions: (PaperPosition & { portfolioId?: string })[]
  label?: string
  startingCash: number
  startingEquity: number
  totalPnl: number
  totalPnlPct: number | null
}

export interface PaperAccount {
  portfolios: Record<string, PaperPortfolio>
  combined: PaperPortfolio
  schedulerJobs: { id: string; nextRun: string | null }[]
}

export interface PaperOverlapEntry {
  symbol: string
  portfolios: {
    portfolioId: string
    planId: number
    entryPrice: number
    openedAt: number
    direction: 'long' | 'short'
    qty: number
    stopLoss: number
    takeProfit: number
    thesis: string | null
  }[]
}

export interface SelectionSnapshot {
  signalType: string | null
  sources: string[]
  techScore: number | null
  streak: number | null
  rsi: number | null
  atrPct: number | null
  investorQualityScore: number | null
  avgAnnualizedGain: number | null
  maxAnnualizedGain: number | null
  buyCount: number | null
  hasOptionsActivity?: boolean
  compositeScore: number | null
}

export interface PaperInstance {
  id: number
  symbol: string
  strategy: string
  params: Record<string, number>
  allocation_usd: number
  enabled: boolean
  portfolio_id?: string
  source_tags?: string[]
  selection_thesis?: string | null
  selection_snapshot?: SelectionSnapshot | null
}

export interface PaperOrder {
  id: number
  instance_id: number | null
  symbol: string
  side: 'buy' | 'sell'
  qty: number
  status: 'proposed' | 'approved' | 'vetoed' | 'filled' | 'error'
  run_kind: string
  note: string | null
  proposed_at: number
  filled_at: number | null
  fill_price: number | null
  verdict: string | null
  size_factor: number | null
  rationale: string | null
  model: string | null
}

export interface PaperRun {
  id: number
  kind: string
  started_at: number
  finished_at: number | null
  proposed: number
  filled: number
  vetoed: number
  errors: string[]
}

export interface AutoSelectSelection {
  symbol: string
  instanceId?: number
  strategy: string
  allocationUsd: number
  signalType: string | null
  sources: string[]
  smartBuy: boolean
  compositeScore: number
  price: number
  atr: number
  selectionThesis: string
  investorQualityScore?: number | null
  avgAnnualizedGain?: number | null
  maxAnnualizedGain?: number | null
  buyCount?: number | null
}

export interface AutoSelectRunLogEntry {
  id: number
  ts: number
  run_kind: string
  candidates: number
  eligible: number
  selected: number
  skipped: string | null
  equity: number | null
  cash: number | null
  selections: AutoSelectSelection[]
  errors: string[]
  bucket_totals: { smart_buy: number; technical_sustained: number; crypto: number } | null
}

export interface PaperPlan {
  id: number
  instance_id: number | null
  portfolio_id?: string
  symbol: string
  entry_order_id: number
  qty: number
  entry_price: number
  opened_at: number
  stop_loss: number
  take_profit: number
  max_hold_days: number
  exit_plan: string
  thesis: string
  levels_source: 'analyst' | 'mechanical'
  status: 'open' | 'closed'
  exit_order_id: number | null
  exit_price: number | null
  exit_reason: string | null
  closed_at: number | null
  realized_pnl: number | null
  realized_pnl_pct: number | null
  verdict: string | null
  size_factor: number | null
  rationale: string | null
  model: string | null
  source_tags: string[]
  selection_thesis: string | null
  selection_snapshot: SelectionSnapshot | null
}

export interface PaperStatsBucket {
  totalClosedTrades: number
  totalRealizedPnl: number
  winRatePct: number | null
  avgWinPct: number | null
  avgLossPct: number | null
  profitFactor: number | null
  avgHoldDays: number | null
  byExitReason: Record<string, { count: number; pnl: number; wins: number }>
  unrealizedPnl: number
  // Whole-account metrics — only populated on the "all" bucket (not decomposable by source)
  sharpe?: number | null
  maxDrawdownPct?: number | null
  annualizedReturnPct?: number | null
  totalReturnPct?: number | null
  periodYears?: number | null
  benchmarkAnnualizedReturnPct?: number | null
  benchmarkTotalReturnPct?: number | null
}

export interface PaperStats {
  all: PaperStatsBucket
  smartBuy: PaperStatsBucket
  technicalSustained: PaperStatsBucket
  crypto: PaperStatsBucket
}

export interface BatchCell {
  symbol: string
  strategy: string
  sharpe?: number | null
  totalReturnPct?: number | null
  maxDrawdownPct?: number | null
  winRatePct?: number | null
  cagrPct?: number | null
  error?: string
}

export interface AutoDeployResult {
  instanceId: number
  symbol: string
  portfolioId?: string
  strategy: string
  strategyLabel: string
  sharpe: number | null
  totalReturnPct: number | null
  winRatePct: number | null
  allocationUsd: number
  period: string
  allBacktests: { strategy: string; label?: string; sharpe?: number | null; error?: string }[]
}

export interface HealthCheckRow {
  instanceId: number
  symbol: string
  strategy: string
  enabled: boolean
  sharpe?: number | null
  totalReturnPct?: number | null
  maxDrawdownPct?: number | null
  recommendation: 'keep' | 'watch' | 'disable' | 'error'
  error?: string
}

export interface PortfolioPosition {
  id: number
  symbol: string
  description: string | null
  qty: number | null
  costPerShare: number | null
  costTotal: number | null
  source?: 'fidelity' | 'manual'
  currentPrice?: number | null
  currentValue?: number | null
  unrealizedPnl?: number | null
  unrealizedPnlPct?: number | null
  score?: number
  trend?: string
  recentCross?: string | null
  rsi?: number | null
  momentum?: string
  macdAboveSignal?: boolean | null
  bollingerStretch?: number | null
  atrPct?: number | null
  stale?: boolean
  assetType?: string
  error?: string
  tier?: 'core' | 'tactical' | 'trade' | null
  exitPlan?: {
    stopLoss: number
    stopLossPct: number
    takeProfit: number
    takeProfitPct: number
    trailingStopPct: number
    dollarRiskIfStopped: number | null
    atr: number
  } | null
}

export interface BrokerConfig {
  broker: string
  alpaca?: { api_key_set: boolean; paper: boolean }
  activeLabel: string
}

export interface ScanResult {
  symbol: string
  score: number
  last?: number
  trend?: string
  recentCross?: string | null
  rsi?: number | null
  momentum?: string
  macdAboveSignal?: boolean | null
  bollingerStretch?: number | null
  atrPct?: number | null
  stale?: boolean
  error?: string
}

export interface SustainedSignal {
  symbol: string
  streak: number
  score: number
  trend?: string
  rsi?: number | null
  macdAboveSignal?: boolean | null
  recentCross?: string | null
  atrPct?: number | null
  lastPrice?: number | null
  stale?: boolean
  lastSeen?: number
  history: { ts: number; score: number }[]
}

export interface ScanWatchlistItem {
  symbol: string
  addedAt: number
}

export interface AutoSelection {
  symbol: string
  instanceId: number
  strategy: string
  allocationUsd: number
  signalType: 'technical' | 'sustained' | 'smart_buy'
  sources: ('technical' | 'sustained' | 'smart_buy' | 'smart_universe' | 'congress')[]
  smartBuy: boolean
  compositeScore: number
  price: number
  atr: number
  investorQualityScore?: number | null
}

export interface AutoSelectResult {
  selected: number
  candidates?: number
  eligible?: number
  selections?: AutoSelection[]
  errors?: string[]
  equity?: number
  cash?: number
  openPositions?: number
  skipped?: string
  ts: number
}

export interface AutoSelectStatus {
  lastResult: AutoSelectResult | null
  scheduledJobs: { id: string; nextRun: string | null }[]
}

export interface SmartBuyAlert {
  ticker: string
  detectedAt: number                // unix timestamp
  scoreAtDetection: number
  currentScore: number | null
  scoreTrend: number[]              // oldest→newest scores since detection
  investorQuality: string
  investorQualityScore: number | null
  politicians: string[]
  buyCount: number
  opportunity: string | null
  assessment: 'new' | 'accumulate' | 'improving' | 'confirmed' | 'stale' | 'deteriorating'
}

export interface CotInstrument {
  code: string
  label: string
  etf: string
  report?: 'tff' | 'disagg'
  hasData: boolean
  reportDate: string | null
  levNet: number | null
  levLong: number | null
  levShort: number | null
  assetNet: number | null
  dealerNet: number | null
  openInterest: number | null
  zScore: number | null
  change1w: number | null
  change4w: number | null
  crowding: 'crowded_long' | 'leaning_long' | 'neutral' | 'leaning_short' | 'crowded_short' | 'no_data'
}

export interface CotSnapshot {
  instruments: CotInstrument[]
  lastRefresh: string | null
  hasData: boolean
}

export interface CotHistoryRow {
  report_date: string
  lev_net: number
  lev_long: number
  lev_short: number
  asset_net: number
  dealer_long: number
  dealer_short: number
  open_interest: number
}

export interface CongressTrade {
  ticker: string
  politician: string
  chamber: 'house' | 'senate'
  txDate: string
  disclosedDate: string
  amount: string
  assetDescription: string
  district: string
  party: string
  // asset type — options trades are parsed from the filing's free-text comment
  assetType?: 'stock' | 'option'
  optionType?: 'call' | 'put' | null
  strikePrice?: number | null
  expirationDate?: string | null
  // enriched price & return fields
  priceAtBuy?: number | null
  priceAtDisclosure?: number | null
  txToDisclosurePct?: number | null
  totalReturnPct?: number | null
  priceAtExit?: number | null
  realized?: boolean
  sellDate?: string | null
}

export interface PoliticianStat {
  trades: number
  wins: number
  winRate: number                   // 0–100, all trades
  avgGain: number                   // raw % from tx date → exit price
  avgAnnualizedGain: number | null  // annualized; null if all holds < 7 days
  // Realized (closed position) breakdown — populated when sell data is available
  realizedCount: number
  openCount: number
  realizedWins: number
  realizedLosses: number
  realizedWinRate: number | null    // null if no sell data
  avgRealizedGain: number | null
  avgOpenGain: number | null
}

export interface CongressTicker {
  ticker: string
  company: string
  buyCount: number
  uniquePoliticians: number
  politicians: string[]
  trades: CongressTrade[]
  // price-move enrichment
  priceAtTx?: number | null
  priceAtDisclosure?: number | null
  priceNow?: number | null
  txToDisclosurePct?: number | null
  disclosureToNowPct?: number | null
  totalMovePct?: number | null
  opportunity?: 'available' | 'fading' | 'gone' | 'pullback' | 'unknown'
  investorQuality?: 'sharp' | 'mixed' | 'weak' | 'unknown'
  investorQualityScore?: number | null
  score?: number | null
  contrarian?: boolean
  hasOptionsActivity?: boolean
  optionsBuyCount?: number
  hasPutActivity?: boolean
  refTxDate?: string
  refDisclosedDate?: string
  enrichError?: string
}

export interface CongressTickerDetail {
  symbol: string
  found: boolean
  ticker: CongressTicker | null
  politicianStats: Record<string, PoliticianStat>
  cacheAgeSec: number
}

export interface CongressSell {
  ticker: string
  politician: string
  chamber: string
  party: string
  txDate: string
  disclosedDate: string
  amount: string
  assetDescription: string
  district: string
}

export interface CongressUniverse {
  tickers: CongressTicker[]
  universe: string[]
  daysBack: number
  minBuys: number
  chamber: string
  cacheInfo: { fetchedAt: number; totalTrades: number; totalSells?: number; errors: string[]; hasKey: boolean; source: string; hasImport: boolean }
  politicianStats: Record<string, PoliticianStat>
  recentSells: CongressSell[]
}

export interface SectorScore {
  etf: string
  name: string
  last: number | null
  ret20: number | null
  ret60: number | null
  momentum: number
  score: number
  trend: string | null
  stale: boolean
  holdings: string[]
  rank: number
  selected: boolean
  error: string | null
}

export interface UniverseResult {
  sectors: SectorScore[]
  universe: string[]
  topN: number
  builtAt: number
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json()
}

async function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const b = await res.json().catch(() => null)
    throw new Error(b?.detail ?? `${res.status} ${res.statusText}`)
  }
  return res.json()
}

export const api = {
  candles: (symbol: string, period: string) =>
    get<{ symbol: string; candles: Candle[]; stale?: boolean }>(
      `/api/ticker/${symbol}/candles?period=${period}`,
    ),
  indicators: (symbol: string, period: string) =>
    get<IndicatorBundle>(`/api/ticker/${symbol}/indicators?period=${period}`),
  vwap: (symbol: string, days = 5) =>
    get<{ symbol: string; candles: Candle[]; vwap: Point[]; upper1: Point[]; lower1: Point[]; upper2: Point[]; lower2: Point[]; stale: boolean }>(
      `/api/ticker/${symbol}/vwap?days=${days}`,
    ),
  signals: (symbol: string) => get<Signals>(`/api/ticker/${symbol}/signals`),
  macro: () => get<MacroSnapshot>('/api/macro/snapshot'),
  gex: (symbol: string, expiration?: string) =>
    get<GexData>(
      `/api/options/${symbol}/gex${expiration ? `?expiration=${expiration}` : ''}`,
    ),
  relvalPresets: () =>
    get<{ pairs: { a: string; b: string; label: string }[] }>('/api/relval/presets'),
  relval: (a: string, b: string, window: number) =>
    get<RelValData>(
      `/api/relval?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&window=${window}`,
    ),
  strategies: () => get<{ strategies: StrategySpec[] }>('/api/backtest/strategies'),
  backtest: (body: {
    symbol: string
    strategy: string
    params?: Record<string, number>
    period?: string
  }) => send<BacktestResult>('/api/backtest', 'POST', body),
  paperAccount: () => get<PaperAccount>('/api/paper/account'),
  paperEquity: () => get<{
    portfolios: Record<string, { curve: { ts: number; equity: number; cash: number }[] }>
    legacy: { curve: { ts: number; equity: number; cash: number }[] }
  }>('/api/paper/equity'),
  paperOverlap: () => get<{ overlaps: PaperOverlapEntry[]; count: number }>('/api/paper/overlap'),
  paperOrders: () => get<{ orders: PaperOrder[] }>('/api/paper/orders'),
  paperRuns: () => get<{ runs: PaperRun[] }>('/api/paper/runs'),
  paperInstances: () => get<{ instances: PaperInstance[] }>('/api/paper/instances'),
  paperCreateInstance: (body: {
    symbol: string
    strategy: string
    params?: Record<string, number>
    allocationUsd: number
    portfolioId: string
  }) => send<{ id: number }>('/api/paper/instances', 'POST', body),
  paperUpdateInstance: (id: number, body: { enabled?: boolean; allocationUsd?: number }) =>
    send<{ ok: boolean }>(`/api/paper/instances/${id}`, 'PATCH', body),
  paperDeleteInstance: (id: number) =>
    send<{ ok: boolean }>(`/api/paper/instances/${id}`, 'DELETE'),
  paperRun: () => send<{
    runId: number
    results: { symbol: string; action: 'hold' | 'filled' | 'vetoed'; desired?: number; held?: number; direction?: string; fillPrice?: number; orderId?: number }[]
    exits: { symbol: string; reason: string; pnl?: number }[]
    errors: string[]
    equity: number
    cash: number
  }>('/api/paper/run', 'POST', { kind: 'manual' }),
  paperPlans: (status = 'all', limit = 100) =>
    get<{ plans: PaperPlan[] }>(`/api/paper/plans?status=${status}&limit=${limit}`),
  paperStats: () => get<PaperStats>('/api/paper/stats'),
  paperAutoSelectLog: (limit = 50) =>
    get<{ runs: AutoSelectRunLogEntry[] }>(`/api/paper/auto-select-log?limit=${limit}`),
  scanTickers: (tickers: string[]) =>
    send<{ results: ScanResult[]; count: number }>('/api/scan', 'POST', { tickers }),
  scanPresets: () => get<{ presets: Record<string, string[]> }>('/api/scan/presets'),
  scanWatchlist: () => get<{ symbols: ScanWatchlistItem[] }>('/api/scan/watchlist'),
  addToScanWatchlist: (symbols: string[]) =>
    send<{ added: number }>('/api/scan/watchlist', 'POST', { symbols }),
  removeFromScanWatchlist: (symbol: string) =>
    send<{ ok: boolean }>(`/api/scan/watchlist/${symbol}`, 'DELETE'),
  runScheduledScan: () => send<{ scanned: number; errors: string[] }>('/api/scan/run-now', 'POST', {}),
  sustainedSignals: (minScore = 2, minScans = 3) =>
    get<{ results: SustainedSignal[]; count: number }>(
      `/api/scan/sustained?minScore=${minScore}&minScans=${minScans}`
    ),
  scanHistory: (symbol: string, days = 30) =>
    get<{ symbol: string; history: { ts: number; score: number; trend?: string; rsi?: number }[] }>(
      `/api/scan/history/${symbol}?days=${days}`
    ),
  cotSnapshot: () => get<CotSnapshot>('/api/cot/snapshot'),
  cotHistory: (code: string, weeks = 156) =>
    get<{ code: string; weeks: number; history: CotHistoryRow[] }>(
      `/api/cot/history/${code}?weeks=${weeks}`
    ),
  cotRefresh: (yearsBack = 3) =>
    send<{ status: string; yearsBack: number }>(`/api/cot/refresh?years_back=${yearsBack}`, 'POST', {}),
  universe: (topN = 2, refresh = false) =>
    get<UniverseResult>(`/api/universe?topN=${topN}&refresh=${refresh ? 1 : 0}`),
  batchBacktest: (tickers: string[], strategies?: string[], period = '2y') =>
    send<{ results: BatchCell[]; tickers: string[]; strategies: string[]; strategyLabels: Record<string, string>; period: string }>(
      '/api/backtest/batch', 'POST', { tickers, strategies, period }
    ),
  tickerNames: (symbols: string[]) =>
    get<{ names: Record<string, string> }>(`/api/ticker/names?symbols=${symbols.join(',')}`),
  congressConfig: () => get<{ hasKey: boolean; hasImport: boolean }>('/api/congress/config'),
  congressSetKey: (key: string) =>
    send<{ ok: boolean; error?: string; cacheInfo: CongressUniverse['cacheInfo'] }>(
      '/api/congress/config', 'POST', { quiver_api_key: key }
    ),
  congressDeleteKey: () => send<{ ok: boolean }>('/api/congress/config', 'DELETE'),
  congressUniverse: (days = 90, minBuys = 1, chamber = 'both') =>
    get<CongressUniverse>(`/api/congress/universe?days=${days}&minBuys=${minBuys}&chamber=${chamber}`),
  congressBuys: (days = 90, chamber = 'both') =>
    get<{ trades: CongressTrade[]; count: number; daysBack: number }>(
      `/api/congress/buys?days=${days}&chamber=${chamber}`
    ),
  congressTickerDetail: (symbol: string) =>
    get<CongressTickerDetail>(`/api/congress/ticker/${encodeURIComponent(symbol)}`),
  congressRefresh: () => send<{ ok: boolean; cacheInfo: CongressUniverse['cacheInfo'] }>('/api/congress/refresh', 'POST', {}),
  congressImportCsv: (csvText: string) =>
    send<{ ok: boolean; rowsParsed: number; warnings: string[]; error?: string; cacheInfo: CongressUniverse['cacheInfo'] }>(
      '/api/congress/import', 'POST', { csv_text: csvText }
    ),
  congressClearImport: () => send<{ ok: boolean }>('/api/congress/import', 'DELETE'),
  autoDeploy: (symbol: string, allocationUsd: number, portfolioId: string, period = '2y') =>
    send<AutoDeployResult>('/api/paper/auto-deploy', 'POST', { symbol, allocationUsd, portfolioId, period }),
  healthCheck: () =>
    send<{ results: HealthCheckRow[]; flagged: number; checked: number }>('/api/paper/health-check', 'POST', {}),
  brokerConfig: () => get<BrokerConfig>('/api/broker/config'),
  setBrokerConfig: (body: { broker: string; alpaca?: { api_key: string; api_secret: string; paper: boolean } }) =>
    send<{ ok: boolean; activeLabel: string }>('/api/broker/config', 'POST', body),
  resetBrokerConfig: () => send<{ ok: boolean; activeLabel: string }>('/api/broker/config/reset', 'POST', {}),
  portfolioImportCsv: (csv: string) =>
    send<{ imported: number; results: PortfolioPosition[] }>('/api/portfolio/import-csv', 'POST', { csv }),
  portfolioImportManual: (positions: { symbol: string; qty?: number; costPerShare?: number }[]) =>
    send<{ imported: number; results: PortfolioPosition[] }>('/api/portfolio/import-manual', 'POST', { positions }),
  portfolioPositions: () => get<{ positions: PortfolioPosition[] }>('/api/portfolio/positions'),
  portfolioAnalyze: () => send<{ results: PortfolioPosition[] }>('/api/portfolio/analyze', 'POST', {}),
  portfolioClear: (source?: 'fidelity' | 'manual') =>
    send<{ ok: boolean }>(`/api/portfolio${source ? `?source=${source}` : ''}`, 'DELETE'),
  portfolioSetTag: (symbol: string, tier: 'core' | 'tactical' | 'trade') =>
    send<{ ok: boolean }>('/api/portfolio/tags', 'POST', { symbol, tier }),
  portfolioDeleteTag: (symbol: string) =>
    send<{ ok: boolean }>(`/api/portfolio/tags/${symbol}`, 'DELETE'),
  scanSmartBuys: (days = 30) =>
    get<{ alerts: SmartBuyAlert[]; count: number; daysBack: number }>(
      `/api/scan/smart-buys?days=${days}`
    ),
  autoSelectStatus: () =>
    get<AutoSelectStatus>('/api/scan/auto-select/status'),
  autoSelectRunNow: () =>
    send<AutoSelectResult>('/api/scan/auto-select/run-now', 'POST', {}),
  paperReset: (portfolio: string) =>
    send<{ ok: boolean; portfolio: string }>(`/api/paper/reset?portfolio=${portfolio}`, 'POST', {}),
  paperResetAll: () =>
    send<{ ok: boolean; resetAll: boolean }>('/api/paper/reset?confirm=all', 'POST', {}),
  dataConfig: () =>
    get<{
      polygonKeySet: boolean
      successorMap: Record<string, string | null>
      analystModel: string
      analystModels: string[]
    }>('/api/ticker/data-config'),
  setPolygonKey: (api_key: string) =>
    send<{ ok: boolean; polygonKeySet: boolean }>('/api/ticker/data-config', 'POST', { api_key }),
  deletePolygonKey: () =>
    send<{ ok: boolean; polygonKeySet: boolean }>('/api/ticker/data-config', 'DELETE'),
  setAnalystModel: (model: string) =>
    send<{ ok: boolean; analystModel: string }>('/api/ticker/analyst-config', 'POST', { model }),
}

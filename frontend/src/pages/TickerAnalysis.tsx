import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, type Candle, type IndicatorBundle, type Signals } from '../api'
import CandleChart, { type Overlay } from '../components/CandleChart'
import LineChart from '../components/LineChart'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

const PERIODS = ['3mo', '6mo', '1y', '2y', '5y']

type OverlayKey = 'sma' | 'ema' | 'bollinger' | 'levels' | 'volume' | 'obv'

const OVERLAY_TIPS: Record<OverlayKey, string> = {
  sma: 'Simple Moving Averages (20/50/200-day) — the average close over the last N days. Price holding above a rising SMA suggests an uptrend; a shorter SMA crossing above/below a longer one (e.g. 50 vs 200) is the classic golden/death cross signal.',
  ema: 'Exponential Moving Averages (9/21-day) — like an SMA but weights recent prices more heavily, so it reacts faster to new price action. Popular for reading short-term trend and momentum.',
  bollinger: 'Bollinger Bands — a 20-day moving average ±2 standard deviations of recent price. Price pressing against an outer band signals a statistically stretched move; band width reflects how volatile the stock currently is.',
  levels: 'Support/Resistance — price levels where the chart has repeatedly reversed (clustered swing highs/lows). More "touches" means a level has been tested more often and is read as stronger.',
  volume: 'Volume bars overlaid at the bottom of the price chart — green when the bar closed up, red when it closed down. Useful for confirming breakouts (high volume) or flagging weak moves (low volume).',
  obv: 'On-Balance Volume — a running total that adds volume on up-days and subtracts it on down-days. Divergence between OBV and price is a classic leading signal: OBV rising while price stalls suggests accumulation.',
}

export default function TickerAnalysis() {
  const [searchParams] = useSearchParams()
  const initSym = (searchParams.get('sym') || 'AAPL').toUpperCase()
  const [symbol, setSymbol] = useState(initSym)
  const [input, setInput] = useState(initSym)
  const [period, setPeriod] = useState('1y')
  const [show, setShow] = useState<Record<OverlayKey, boolean>>({
    sma: true,
    ema: false,
    bollinger: false,
    levels: true,
    volume: false,
    obv: false,
  })
  const [candles, setCandles] = useState<Candle[]>([])
  const [ind, setInd] = useState<IndicatorBundle | null>(null)
  const [signals, setSignals] = useState<Signals | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (sym: string, per: string) => {
    setLoading(true)
    setError(null)
    try {
      const [c, i, s] = await Promise.all([
        api.candles(sym, per),
        api.indicators(sym, per),
        api.signals(sym),
      ])
      setCandles(c.candles)
      setInd(i)
      setSignals(s)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(symbol, period)
  }, [symbol, period, load])

  const overlays = useMemo<Overlay[]>(() => {
    if (!ind) return []
    const out: Overlay[] = []
    if (show.sma) {
      out.push({ points: ind.sma20, color: '#58a6ff', title: 'SMA20' })
      out.push({ points: ind.sma50, color: '#d29922', title: 'SMA50' })
      out.push({ points: ind.sma200, color: '#bc8cff', title: 'SMA200' })
    }
    if (show.ema) {
      out.push({ points: ind.ema9, color: '#39d2c0', title: 'EMA9' })
      out.push({ points: ind.ema21, color: '#ff9bce', title: 'EMA21' })
    }
    if (show.bollinger) {
      out.push({ points: ind.bbUpper, color: '#8b949e', dashed: true, title: 'BB+2σ' })
      out.push({ points: ind.bbLower, color: '#8b949e', dashed: true, title: 'BB-2σ' })
    }
    return out
  }, [ind, show])

  const levels = useMemo(() => (show.levels && ind ? ind.levels : []), [ind, show.levels])

  const rsiLines = useMemo(
    () => (ind ? [{ points: ind.rsi, color: '#58a6ff', title: 'RSI(14)' }] : []),
    [ind],
  )
  const rsiGuides = useMemo(
    () => [
      { value: 70, color: '#f85149', title: '70' },
      { value: 30, color: '#3fb950', title: '30' },
    ],
    [],
  )
  const macdLines = useMemo(
    () =>
      ind
        ? [
            { points: ind.macd, color: '#58a6ff', title: 'MACD' },
            { points: ind.macdSignal, color: '#d29922', title: 'Signal' },
          ]
        : [],
    [ind],
  )

  const obvLines = useMemo(
    () => (ind && show.obv ? [{ points: ind.obv, color: '#bc8cff', title: 'OBV' }] : []),
    [ind, show.obv],
  )

  const toggle = (k: OverlayKey) => setShow((s) => ({ ...s, [k]: !s[k] }))

  const trendTone = signals?.trend === 'uptrend' ? 'pos' : signals?.trend === 'downtrend' ? 'neg' : 'neutral'
  const momTone = signals?.momentum === 'overbought' ? 'neg' : signals?.momentum === 'oversold' ? 'pos' : 'neutral'

  return (
    <div>
      <h1>Ticker Analysis</h1>
      <div className="controls">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (input.trim()) setSymbol(input.trim().toUpperCase())
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ticker e.g. AAPL"
            style={{ width: 130 }}
          />
          <button className="primary" type="submit" disabled={loading} style={{ marginLeft: 8 }}>
            {loading ? 'Loading…' : 'Analyze'}
          </button>
        </form>
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          {PERIODS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        {(['sma', 'ema', 'bollinger', 'levels', 'volume', 'obv'] as OverlayKey[]).map((k) => (
          <label key={k} style={{ fontSize: 13, color: show[k] ? '#e6edf3' : '#8b949e' }}>
            <input type="checkbox" checked={show[k]} onChange={() => toggle(k)} /> {k.toUpperCase()}
            <Tip text={OVERLAY_TIPS[k]} />
          </label>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}
      {ind?.stale && (
        <div className="stale-banner">Data source unreachable — showing last cached data.</div>
      )}

      {signals && !error && (
        <div className="tiles">
          <StatTile label="Last" value={signals.last} sub={signals.symbol}
            tooltip="Most recent closing price." />
          <StatTile label="Trend" value={signals.trend} tone={trendTone}
            sub={signals.recentCross ? signals.recentCross.replace('_', ' ') : undefined}
            tooltip="Read from moving averages: 'uptrend' means price is above both the 50- and 200-day SMA with the 50 above the 200 (mirror image for 'downtrend'). The sub-label flags a golden/death cross — the 50-day SMA crossing above/below the 200-day — within the last 10 bars." />
          <StatTile label="RSI (14)" value={signals.rsi} tone={momTone} sub={signals.momentum}
            tooltip="Relative Strength Index over 14 periods — a 0-100 momentum oscillator. Above 70 is commonly read as overbought, below 30 as oversold. Strong trends can stay at extremes for a while, so treat this as a stretch gauge, not a standalone reversal signal." />
          <StatTile
            label="MACD"
            value={signals.macdAboveSignal == null ? '—' : signals.macdAboveSignal ? 'above signal' : 'below signal'}
            tone={signals.macdAboveSignal == null ? 'neutral' : signals.macdAboveSignal ? 'pos' : 'neg'}
            tooltip="Moving Average Convergence/Divergence — the gap between the 12- and 26-day EMA (the MACD line) versus that line's own 9-day EMA (the signal line). Line above signal favors bullish momentum; below favors bearish."
          />
          <StatTile label="Bollinger stretch" value={signals.bollingerStretch}
            sub="±1 = band edge" tone={
              signals.bollingerStretch != null && Math.abs(signals.bollingerStretch) > 1 ? 'warn' : 'neutral'
            }
            tooltip="Where price sits inside its Bollinger Bands: 0 = the middle 20-day average, ±1 = the outer bands. Beyond ±1 means price has pushed outside the bands — a statistically extended move that often (not always) mean-reverts." />
          <StatTile label="ATR" value={signals.atrPct != null ? `${signals.atrPct}%` : null} sub="daily range"
            tooltip="Average True Range as a % of price — a volatility measure of typical daily movement (gaps included). Higher = bigger average swings; often used to size stop-losses or position size." />
        </div>
      )}

      {candles.length > 0 && !error && (
        <div className="panel" style={{ marginBottom: 16 }}>
          <h2>
            Price
            <Tip text="Candlesticks: green = close above open, red = close below open. Dashed green/red horizontal lines are the strongest support/resistance levels. Overlay colors match the checkboxes above (SMA20 blue, SMA50 amber, SMA200 purple; EMA9 teal, EMA21 pink; Bollinger bands gray dashed). VOLUME adds semi-transparent bars at the bottom of the price pane." />
          </h2>
          <CandleChart candles={candles} overlays={overlays} levels={levels} showVolume={show.volume} />
        </div>
      )}

      {ind && !error && (
        <div className="row">
          <div className="col panel">
            <h2>
              RSI (14)
              <Tip text="Momentum oscillator, 0-100. Dashed guide lines mark the conventional 70 (overbought) and 30 (oversold) thresholds." />
            </h2>
            <LineChart lines={rsiLines} guides={rsiGuides} height={200} />
          </div>
          <div className="col panel">
            <h2>
              MACD (12, 26, 9)
              <Tip text="Blue = MACD line (12-day EMA minus 26-day EMA). Amber = signal line (9-day EMA of the MACD line). Momentum turns bullish when blue crosses above amber, bearish when it crosses below." />
            </h2>
            <LineChart lines={macdLines} height={200} />
          </div>
        </div>
      )}

      {ind && show.obv && !error && (
        <div className="panel" style={{ marginTop: 16 }}>
          <h2>
            OBV — On-Balance Volume
            <Tip text="Running cumulative volume: up-day volume is added, down-day volume is subtracted. An OBV trend that leads or confirms the price trend signals conviction. Divergence (OBV falling while price holds) is a bearish warning." />
          </h2>
          <LineChart lines={obvLines} height={180} />
        </div>
      )}
    </div>
  )
}

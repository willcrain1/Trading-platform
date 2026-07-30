import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type RelValData } from '../api'
import LineChart from '../components/LineChart'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

export default function RelValue() {
  const [presets, setPresets] = useState<{ a: string; b: string; label: string }[]>([])
  const [a, setA] = useState('QQQ')
  const [b, setB] = useState('SPY')
  const [inputA, setInputA] = useState('QQQ')
  const [inputB, setInputB] = useState('SPY')
  const [window, setWindow] = useState(60)
  const [data, setData] = useState<RelValData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.relvalPresets().then((p) => setPresets(p.pairs)).catch(() => {})
  }, [])

  const load = useCallback(async (pa: string, pb: string, win: number) => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.relval(pa, pb, win))
    } catch (e) {
      setError((e as Error).message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(a, b, window)
  }, [a, b, window, load])

  const ratioLines = useMemo(
    () => (data ? [{ points: data.ratio, color: '#58a6ff', title: `${data.a}/${data.b}` }] : []),
    [data],
  )
  const zLines = useMemo(
    () => (data ? [{ points: data.zscore, color: '#d29922', title: 'z-score' }] : []),
    [data],
  )
  const zGuides = useMemo(
    () => [
      { value: 2, color: '#f85149', title: '+2σ' },
      { value: 0, color: '#8b949e' },
      { value: -2, color: '#3fb950', title: '-2σ' },
    ],
    [],
  )
  const corrLines = useMemo(
    () => (data ? [{ points: data.correlation, color: '#bc8cff', title: 'corr' }] : []),
    [data],
  )

  const read = data?.current.read
  const readLabel =
    read === 'stretched_rich' ? `${data?.a} rich vs ${data?.b}` :
    read === 'stretched_cheap' ? `${data?.a} cheap vs ${data?.b}` : 'within normal range'

  return (
    <div>
      <h1>Relative Value</h1>
      <div className="controls">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (inputA.trim() && inputB.trim()) {
              setA(inputA.trim().toUpperCase())
              setB(inputB.trim().toUpperCase())
            }
          }}
        >
          <input value={inputA} onChange={(e) => setInputA(e.target.value)} style={{ width: 100 }} />
          <span className="muted" style={{ margin: '0 6px' }}>vs</span>
          <input value={inputB} onChange={(e) => setInputB(e.target.value)} style={{ width: 100 }} />
          <button className="primary" type="submit" disabled={loading} style={{ marginLeft: 8 }}>
            {loading ? 'Loading…' : 'Compare'}
          </button>
        </form>
        <select value={window} onChange={(e) => setWindow(Number(e.target.value))}>
          <option value={30}>30d window</option>
          <option value={60}>60d window</option>
          <option value={120}>120d window</option>
        </select>
        <select
          value=""
          onChange={(e) => {
            const p = presets[Number(e.target.value)]
            if (p) {
              setInputA(p.a); setInputB(p.b); setA(p.a); setB(p.b)
            }
          }}
        >
          <option value="" disabled>Preset pairs…</option>
          {presets.map((p, i) => (
            <option key={p.label} value={i}>{p.label}</option>
          ))}
        </select>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {data?.stale && (
        <div className="stale-banner">Served from cache — data source unreachable.</div>
      )}

      {data && !error && (
        <>
          <div className="tiles">
            <StatTile label="Ratio" value={data.current.ratio} sub={`${data.a}/${data.b}`}
              tooltip={`Price of ${data.a} divided by price of ${data.b}. A rising ratio means ${data.a} is outperforming ${data.b} (or ${data.b} underperforming); this is the raw relative-value view before any statistics are applied.`} />
            <StatTile
              label={`Z-score (${data.window}d)`}
              value={data.current.zscore}
              sub={readLabel}
              tone={read === 'stretched_rich' ? 'neg' : read === 'stretched_cheap' ? 'pos' : 'neutral'}
              tooltip="How many standard deviations the current log-spread (log(A) − log(B)) sits from its own rolling average. Beyond ±2 is read as statistically stretched — the classic pairs-trade setup: short the outperformer, long the underperformer, betting the spread reverts."
            />
            <StatTile label="Correlation" value={data.current.correlation} sub="rolling daily returns"
              tooltip="Rolling correlation of daily returns between A and B over the selected window. High correlation (near 1) supports treating the two as a genuine relative-value pair rather than two unrelated assets that happen to be compared." />
            <StatTile
              label="Mean-reversion half-life"
              value={data.current.halfLifeDays != null ? `${data.current.halfLifeDays}d` : 'n/a'}
              sub={data.current.halfLifeDays == null ? 'not mean-reverting' : 'OU fit on log-spread'}
              tooltip="Estimated days for the spread to revert halfway back to its mean, from an Ornstein-Uhlenbeck (AR(1)) regression on the log-spread. Shorter = faster, more tradeable mean reversion. 'Not mean-reverting' means the fit found no pull back toward the mean over this window."
            />
          </div>
          <div className="panel" style={{ marginBottom: 16 }}>
            <h2>
              Price ratio {data.a}/{data.b}
              <Tip text="The two prices divided, plotted over time. Rising = A outperforming B; falling = B outperforming A." />
            </h2>
            <LineChart lines={ratioLines} height={240} />
          </div>
          <div className="row">
            <div className="col panel">
              <h2>
                Log-spread z-score
                <Tip text="Standardized distance of log(A) − log(B) from its rolling mean. Dashed guides at ±2σ mark the conventional 'stretched' threshold used for mean-reversion pair trades." />
              </h2>
              <LineChart lines={zLines} guides={zGuides} height={220} />
            </div>
            <div className="col panel">
              <h2>
                Rolling correlation
                <Tip text="How closely A and B's daily returns have moved together over the trailing window, from -1 (perfectly opposite) to +1 (perfectly together). A pair trade works best when this stays consistently high." />
              </h2>
              <LineChart lines={corrLines} height={220} />
            </div>
          </div>
        </>
      )}
    </div>
  )
}

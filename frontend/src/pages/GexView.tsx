import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type GexData } from '../api'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

function fmtGex(v: number): string {
  const abs = Math.abs(v)
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (abs >= 1e3) return `${(v / 1e3).toFixed(0)}K`
  return v.toFixed(0)
}

export default function GexView() {
  const [symbol, setSymbol] = useState('SPY')
  const [input, setInput] = useState('SPY')
  const [expiration, setExpiration] = useState<string | undefined>(undefined)
  const [data, setData] = useState<GexData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (sym: string, exp?: string) => {
    setLoading(true)
    setError(null)
    try {
      setData(await api.gex(sym, exp))
    } catch (e) {
      setError((e as Error).message)
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(symbol, expiration)
  }, [symbol, expiration, load])

  // window strikes to +/-15% around spot so the chart stays readable
  const visible = useMemo(() => {
    if (!data) return []
    const lo = data.spot * 0.85
    const hi = data.spot * 1.15
    return data.strikes.filter((s) => s.strike >= lo && s.strike <= hi && s.netGex !== 0)
  }, [data])

  const maxAbs = useMemo(
    () => Math.max(1, ...visible.map((s) => Math.abs(s.netGex))),
    [visible],
  )

  return (
    <div>
      <h1>Options Positioning — Gamma Exposure</h1>
      <div className="controls">
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (input.trim()) {
              setExpiration(undefined)
              setSymbol(input.trim().toUpperCase())
            }
          }}
        >
          <input value={input} onChange={(e) => setInput(e.target.value)} style={{ width: 130 }} />
          <button className="primary" type="submit" disabled={loading} style={{ marginLeft: 8 }}>
            {loading ? 'Loading…' : 'Load'}
          </button>
        </form>
        {data && (
          <select value={data.expiration} onChange={(e) => setExpiration(e.target.value)}>
            {data.expirations.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        )}
      </div>

      {error && <div className="error-banner">{error}</div>}
      {data?.stale && (
        <div className="stale-banner">Options chain served from cache — data source unreachable.</div>
      )}

      {data && !error && (
        <>
          <div className="tiles">
            <StatTile label="Spot" value={data.spot.toFixed(2)} sub={data.symbol}
              tooltip="Current/last traded price of the underlying, from the live quote." />
            <StatTile
              label="Net GEX"
              value={fmtGex(data.netGexTotal)}
              tone={data.netGexTotal >= 0 ? 'pos' : 'neg'}
              sub={data.netGexTotal >= 0 ? 'dealers dampen moves' : 'dealers amplify moves'}
              tooltip="Sum of dealer gamma exposure across all strikes for this expiration. Positive net GEX implies dealers are typically long gamma and hedge by buying dips/selling rallies — dampening volatility. Negative net GEX implies dealers hedge by selling into drops and buying into rallies — amplifying moves."
            />
            <StatTile
              label="Gamma flip"
              value={data.flipPoint}
              sub={data.flipPoint != null ? (data.spot > data.flipPoint ? 'spot above flip' : 'spot below flip') : undefined}
              tone={data.flipPoint != null && data.spot < data.flipPoint ? 'warn' : 'neutral'}
              tooltip="The strike where cumulative net GEX crosses from negative to positive, scanning low to high strikes. Markets are often calmer above this level (positive-gamma regime) and choppier below it (negative-gamma regime)."
            />
            <StatTile label="Max pain" value={data.maxPain} sub={data.expiration}
              tooltip="The strike at which total intrinsic value paid out to all option holders would be smallest — i.e. where option sellers (often dealers) lose the least. Price is sometimes said to drift toward max pain into expiration, but the evidence for this is mixed." />
            <StatTile
              label="Put/Call OI"
              value={data.putCallOiRatio}
              tone={data.putCallOiRatio != null && data.putCallOiRatio > 1 ? 'warn' : 'neutral'}
              tooltip="Total put open interest ÷ total call open interest for this expiration. Above 1 means more open puts than calls outstanding — can reflect hedging demand or bearish positioning, but is a positioning gauge, not a reliable directional signal on its own."
            />
          </div>

          <div className="row">
            <div className="col panel" style={{ minWidth: 420 }}>
              <h2>
                Net GEX by strike (±15% of spot)
                <Tip text="Bars show net gamma exposure at each strike: Black-Scholes gamma × open interest × 100 × spot, calls contributing positive GEX and puts negative, under the standard assumption dealers are short puts / long calls. The highlighted strike is closest to spot; bold strikes are the largest-magnitude levels (right panel)." />
              </h2>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#8b949e', marginBottom: 6 }}>
                <span>← put-dominated (negative)</span>
                <span>call-dominated (positive) →</span>
              </div>
              {[...visible].reverse().map((s) => {
                const isSpot = Math.abs(s.strike - data.spot) === Math.min(...visible.map((v) => Math.abs(v.strike - data.spot)))
                const isKey = data.keyLevels.includes(s.strike)
                const w = (Math.abs(s.netGex) / maxAbs) * 100
                return (
                  <div className="gexbar-row" key={s.strike}
                    style={isSpot ? { background: 'rgba(88,166,255,0.12)', borderRadius: 3 } : undefined}>
                    <div className="gexbar-strike" style={isKey ? { color: '#e6edf3', fontWeight: 700 } : undefined}>
                      {s.strike}
                    </div>
                    <div className="gexbar-track">
                      <div className="gexbar-half">
                        {s.netGex < 0 && (
                          <div className="gexbar-fill" style={{ right: 0, width: `${w}%`, background: '#f85149' }} />
                        )}
                      </div>
                      <div className="gexbar-half" style={{ borderLeft: '1px solid #30363d' }}>
                        {s.netGex >= 0 && (
                          <div className="gexbar-fill" style={{ left: 0, width: `${w}%`, background: '#3fb950' }} />
                        )}
                      </div>
                    </div>
                    <div style={{ width: 64, color: '#8b949e' }}>{fmtGex(s.netGex)}</div>
                  </div>
                )
              })}
            </div>
            <div className="col panel" style={{ maxWidth: 360 }}>
              <h2>Dealer-informed levels</h2>
              <p className="muted" style={{ fontSize: 13, marginTop: 0 }}>
                Largest |GEX| strikes — where dealer hedging flow concentrates. These often act as
                support/resistance magnets into expiration.
              </p>
              <table className="data">
                <thead><tr><th>Strike</th><th>vs spot</th></tr></thead>
                <tbody>
                  {data.keyLevels.map((k) => (
                    <tr key={k}>
                      <td>{k}</td>
                      <td className={k >= data.spot ? 'neg' : 'pos'}>
                        {(((k - data.spot) / data.spot) * 100).toFixed(2)}%
                        {' '}{k >= data.spot ? 'above' : 'below'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12 }}>
                GEX = BS gamma × OI × 100 × spot (calls +, puts −), assuming the standard
                dealer-long-calls / short-puts positioning convention. Open interest updates
                overnight; intraday flow is not visible in this data.
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

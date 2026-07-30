import { useEffect, useState } from 'react'
import { api, type MacroSnapshot } from '../api'
import Sparkline from '../components/Sparkline'
import StatTile from '../components/StatTile'
import Tip from '../components/Tip'

export default function MacroDashboard() {
  const [snap, setSnap] = useState<MacroSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.macro().then(setSnap).catch((e) => setError((e as Error).message))
  }, [])

  if (error) return <div><h1>Macro Dashboard</h1><div className="error-banner">{error}</div></div>
  if (!snap) return <div><h1>Macro Dashboard</h1><div className="loading">Loading cross-asset data…</div></div>

  const groups = [...new Set(snap.assets.map((a) => a.group))]
  const rc = snap.riskComposite

  return (
    <div>
      <h1>Macro Dashboard</h1>
      {snap.stale && (
        <div className="stale-banner">Some assets served from cache — data source unreachable.</div>
      )}
      <div className="tiles">
        <StatTile
          label="Risk regime"
          value={rc == null ? '—' : rc > 0.15 ? 'Risk-on' : rc < -0.15 ? 'Risk-off' : 'Mixed'}
          sub={rc != null ? `composite ${rc}` : undefined}
          tone={rc == null ? 'neutral' : rc > 0.15 ? 'pos' : rc < -0.15 ? 'neg' : 'warn'}
          tooltip="A simple gauge combining 20-day returns of risk-on assets (equities, copper, crude, bitcoin) against risk-off assets (gold, dollar, VIX), each z-scored against this basket. Positive = risk appetite building; negative = flight to safety. This is a basic heuristic, not a validated trading model."
        />
        <StatTile
          label="Curve (10Y − 3M)"
          value={snap.curve10y3m != null ? `${snap.curve10y3m}%` : null}
          sub={snap.curve10y3m != null && snap.curve10y3m < 0 ? 'inverted' : 'normal slope'}
          tone={snap.curve10y3m != null && snap.curve10y3m < 0 ? 'neg' : 'pos'}
          tooltip="10-year Treasury yield minus the 13-week (3-month) yield. A negative reading ('inversion') has historically preceded U.S. recessions, though with an unreliable lag of roughly 6-18 months — not a precise timing tool."
        />
        {snap.dispersion && (
          <StatTile
            label="VIXEQ − VIX (dispersion)"
            value={`${snap.dispersion.spread >= 0 ? '+' : ''}${snap.dispersion.spread} pts`}
            sub={`≈ ${(snap.dispersion.impliedCorrelation * 100).toFixed(0)}% implied correlation`}
            tone={
              snap.dispersion.impliedCorrelation < 0.2 ? 'pos' :
              snap.dispersion.impliedCorrelation > 0.45 ? 'neg' : 'warn'
            }
            tooltip="VIX prices 30-day implied volatility of the S&P 500 index; VIXEQ prices the average 30-day implied volatility of its individual constituents. Diversification normally makes index vol lower than single-stock vol, so VIXEQ sits above VIX — the gap between them is a rough gauge of expected correlation: (VIX/VIXEQ)² approximates 'implied correlation'. A WIDE spread (low implied correlation) means stocks are trading on their own company-specific news — a dispersion / stock-picker's regime. A NARROWING spread (rising implied correlation) means stocks are increasingly moving together on macro factors — often an early warning that a broad, correlated risk-off move is building, since diversification stops helping exactly when it's needed most. This free data feed only provides VIXEQ's current spot level, not history, so no 20-day trend is shown."
          />
        )}
      </div>
      {groups.map((g) => (
        <div className="panel" key={g} style={{ marginBottom: 16 }}>
          <h2>{g}</h2>
          <table className="data">
            <thead>
              <tr>
                <th>Asset</th>
                <th>
                  Last
                  <Tip text="Most recent price/level for this instrument." />
                </th>
                <th>
                  1d %
                  <Tip text="Percent change from the previous session's close." />
                </th>
                <th>
                  20d %
                  <Tip text="Percent change over the last 20 trading days (~1 month) — the medium-term momentum figure used to build the risk-regime composite above." />
                </th>
                <th>
                  20d trend
                  <Tip text="Sparkline of the last 20 daily closes — green if this window's move is up, red if down." />
                </th>
              </tr>
            </thead>
            <tbody>
              {snap.assets.filter((a) => a.group === g).map((a) => (
                <tr key={a.symbol}>
                  <td>{a.name} <span className="muted">({a.symbol})</span></td>
                  <td>{a.last.toLocaleString()}</td>
                  <td className={a.changePct >= 0 ? 'pos' : 'neg'}>
                    {a.changePct >= 0 ? '+' : ''}{a.changePct}%
                  </td>
                  <td className={a.ret20Pct >= 0 ? 'pos' : 'neg'}>
                    {a.ret20Pct >= 0 ? '+' : ''}{a.ret20Pct}%
                  </td>
                  <td><Sparkline values={a.spark} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      <p className="muted" style={{ fontSize: 12 }}>
        Yields (^TNX, ^FVX, ^IRX) quoted in percent. Risk composite is a
        cross-sectional z-score of 20-day returns, signed by each asset's risk direction
        (equities/copper/oil/BTC risk-on; gold/dollar/VIX risk-off). The implied-correlation
        proxy (VIX/VIXEQ)² is a rough back-of-envelope heuristic, not CBOE's official
        implied-correlation methodology.
      </p>
    </div>
  )
}

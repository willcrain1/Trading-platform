import { useEffect, useMemo, useState } from 'react'
import { api, type CotHistoryRow, type CotInstrument, type CotSnapshot } from '../api'
import Tip from '../components/Tip'

// ── helpers ────────────────────────────────────────────────────────────────────

function fmtK(v: number | null): string {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const sign = v >= 0 ? '+' : '-'
  if (abs >= 1000) return `${sign}${(abs / 1000).toFixed(0)}K`
  return `${sign}${abs.toFixed(0)}`
}

function fmtDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function crowdingColor(c: string): string {
  if (c === 'crowded_long')  return '#f85149'
  if (c === 'leaning_long')  return '#d29922'
  if (c === 'crowded_short') return '#3fb950'
  if (c === 'leaning_short') return '#58a6ff'
  return '#8b949e'
}

function crowdingLabel(c: string): string {
  if (c === 'crowded_long')  return 'Crowded Long'
  if (c === 'leaning_long')  return 'Leaning Long'
  if (c === 'crowded_short') return 'Crowded Short'
  if (c === 'leaning_short') return 'Leaning Short'
  if (c === 'no_data')       return 'No data'
  return 'Neutral'
}

function crowdingNote(c: string): string {
  if (c === 'crowded_long')  return 'Trade is full — reversal risk elevated, late to join'
  if (c === 'leaning_long')  return 'Positioning skewed long but not extreme'
  if (c === 'crowded_short') return 'Short squeeze potential — shorts at risk of forced cover'
  if (c === 'leaning_short') return 'Positioning skewed short but not extreme'
  return 'Balanced — no strong directional crowding signal'
}

// ── ZScoreGauge ───────────────────────────────────────────────────────────────
// Horizontal bar from -3 to +3 showing crowding vs. history.
// Positive (crowded long) = amber/red. Negative (crowded short) = steel blue.

function ZScoreGauge({ z }: { z: number | null }) {
  if (z == null) return <span className="muted" style={{ fontSize: 12 }}>No data</span>

  const clamped = Math.max(-3, Math.min(3, z))
  const totalW = 180
  const center = totalW / 2
  const scale = center / 3  // pixels per z unit
  const barW = Math.abs(clamped) * scale
  const barX = clamped >= 0 ? center : center - barW
  const fill = clamped >= 0 ? '#d29922' : '#58a6ff'
  const extremeFill = Math.abs(clamped) >= 2
    ? (clamped >= 0 ? '#f85149' : '#3fb950')
    : fill

  return (
    <div>
      <svg width={totalW} height={20} style={{ display: 'block', overflow: 'visible' }}>
        {/* track */}
        <rect x={0} y={6} width={totalW} height={8} fill="#21262d" rx={4} />
        {/* threshold markers at ±1 and ±2 */}
        {[-2, -1, 1, 2].map((t) => (
          <line key={t}
            x1={center + t * scale} y1={4}
            x2={center + t * scale} y2={16}
            stroke="#30363d" strokeWidth={1} />
        ))}
        {/* center line */}
        <line x1={center} y1={3} x2={center} y2={17} stroke="#484f58" strokeWidth={1.5} />
        {/* fill bar */}
        {barW > 0 && (
          <rect x={barX} y={6} width={barW} height={8} fill={extremeFill} rx={2} opacity={0.9} />
        )}
        {/* cursor dot */}
        <circle cx={center + clamped * scale} cy={10} r={4} fill={extremeFill} />
      </svg>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#484f58', marginTop: 1 }}>
        <span>−3</span><span>−2</span><span>−1</span><span style={{ color: '#8b949e' }}>0</span><span>+1</span><span>+2</span><span>+3</span>
      </div>
    </div>
  )
}

// ── MiniNetChart ──────────────────────────────────────────────────────────────
// Inline SVG sparkline of historical lev-money net positions (oldest → newest).

function MiniNetChart({ history }: { history: CotHistoryRow[] }) {
  const pts = [...history].reverse()  // chronological
  if (pts.length < 2) return null
  const nets = pts.map((r) => r.lev_net)
  const min = Math.min(...nets), max = Math.max(...nets)
  const range = max - min || 1
  const w = 240, h = 60
  const xStep = w / (pts.length - 1)
  const yFor = (v: number) => h - ((v - min) / range) * (h - 4) - 2
  const zero = yFor(0)

  const pathD = pts.map((r, i) => `${i === 0 ? 'M' : 'L'}${(i * xStep).toFixed(1)},${yFor(r.lev_net).toFixed(1)}`).join(' ')

  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {/* zero line */}
      {zero >= 0 && zero <= h && (
        <line x1={0} y1={zero} x2={w} y2={zero} stroke="#30363d" strokeWidth={1} strokeDasharray="3,3" />
      )}
      {/* extreme zone markers at ±1σ would require passing stats — omit for simplicity */}
      <path d={pathD} fill="none" stroke="#58a6ff" strokeWidth={1.5} />
      {/* area fill above/below zero */}
      {zero >= 0 && zero <= h && (
        <>
          <path
            d={`${pathD} L${((pts.length - 1) * xStep).toFixed(1)},${zero} L0,${zero} Z`}
            fill="#58a6ff" opacity={0.08}
          />
        </>
      )}
      {/* latest dot */}
      <circle cx={(pts.length - 1) * xStep} cy={yFor(nets[nets.length - 1])} r={3}
        fill={nets[nets.length - 1] >= 0 ? '#d29922' : '#58a6ff'} />
    </svg>
  )
}

// ── InstrumentCard ─────────────────────────────────────────────────────────────

function InstrumentCard({
  inst, selected, onClick,
}: {
  inst: CotInstrument
  selected: boolean
  onClick: () => void
}) {
  const borderColor = selected ? '#58a6ff' : (
    inst.hasData ? crowdingColor(inst.crowding) : '#21262d'
  )

  return (
    <div
      onClick={onClick}
      style={{
        border: `1px solid ${borderColor}`,
        borderRadius: 8,
        padding: '14px 16px',
        cursor: 'pointer',
        background: selected ? 'rgba(88,166,255,0.06)' : '#0d1117',
        opacity: inst.hasData ? 1 : 0.4,
        transition: 'border-color 0.2s, background 0.2s',
      }}
    >
      {/* header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15, color: '#e6edf3' }}>{inst.label}</div>
          <div style={{ fontSize: 11, color: '#8b949e' }}>{inst.etf} · {fmtDate(inst.reportDate)}</div>
        </div>
        <span style={{
          fontSize: 11, fontWeight: 700, padding: '3px 8px', borderRadius: 12,
          background: crowdingColor(inst.crowding) + '22',
          color: crowdingColor(inst.crowding),
          whiteSpace: 'nowrap',
        }}>
          {crowdingLabel(inst.crowding)}
        </span>
      </div>

      {/* z-score gauge */}
      <ZScoreGauge z={inst.zScore} />

      {/* metrics row */}
      {inst.hasData && (
        <div style={{ display: 'flex', gap: 16, marginTop: 10, fontSize: 12 }}>
          <div>
            <div className="muted" style={{ fontSize: 10 }}>Lev net</div>
            <strong style={{ color: (inst.levNet ?? 0) >= 0 ? '#d29922' : '#58a6ff' }}>
              {fmtK(inst.levNet)}
            </strong>
          </div>
          <div>
            <div className="muted" style={{ fontSize: 10 }}>1w Δ</div>
            <span style={{ color: (inst.change1w ?? 0) >= 0 ? '#3fb950' : '#f85149' }}>
              {fmtK(inst.change1w)}
            </span>
          </div>
          <div>
            <div className="muted" style={{ fontSize: 10 }}>4w Δ</div>
            <span style={{ color: (inst.change4w ?? 0) >= 0 ? '#3fb950' : '#f85149' }}>
              {fmtK(inst.change4w)}
            </span>
          </div>
          <div>
            <div className="muted" style={{ fontSize: 10 }}>Asset mgr</div>
            <span style={{ color: (inst.assetNet ?? 0) >= 0 ? '#56d364' : '#ffa198', fontSize: 11 }}>
              {fmtK(inst.assetNet)}
            </span>
          </div>
        </div>
      )}

      {inst.hasData && (
        <div style={{ fontSize: 11, color: crowdingColor(inst.crowding), marginTop: 8, fontStyle: 'italic' }}>
          {crowdingNote(inst.crowding)}
        </div>
      )}
    </div>
  )
}

// ── DetailPanel ───────────────────────────────────────────────────────────────

function DetailPanel({ inst }: { inst: CotInstrument }) {
  const [history, setHistory] = useState<CotHistoryRow[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!inst.hasData) return
    setLoading(true)
    api.cotHistory(inst.code, 156).then((r) => {
      setHistory(r.history)
    }).catch(() => {}).finally(() => setLoading(false))
  }, [inst.code])

  const pts = useMemo(() => [...history].reverse(), [history])
  const nets = pts.map((r) => r.lev_net)

  // Full SVG chart: net position + ±1σ/±2σ bands
  const w = 600, h = 140
  const chartData = useMemo(() => {
    if (nets.length < 4) return null
    const min = Math.min(...nets), max = Math.max(...nets)
    const range = max - min || 1
    const mean = nets.reduce((a, b) => a + b, 0) / nets.length
    const std = Math.sqrt(nets.reduce((a, b) => a + (b - mean) ** 2, 0) / nets.length)
    const pad = 8
    const xStep = (w - pad * 2) / Math.max(1, nets.length - 1)
    const yFor = (v: number) => h - pad - ((v - min) / range) * (h - pad * 2)
    const zeroY = yFor(0)
    const line = pts.map((r, i) =>
      `${i === 0 ? 'M' : 'L'}${(pad + i * xStep).toFixed(1)},${yFor(r.lev_net).toFixed(1)}`
    ).join(' ')
    return { min, max, mean, std, xStep, yFor, zeroY, line, pad }
  }, [nets, pts, w, h])

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <h2 style={{ marginBottom: 4 }}>{inst.label} — Positioning History</h2>
      <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>
        {inst.report === 'disagg'
          ? 'Managed Money (hedge fund) net contracts from the Disaggregated report.'
          : 'Leveraged Money (hedge fund) net contracts from the TFF report.'}{' '}
        Positive = net long, negative = net short. Z-score over trailing 52 weeks.
        Current z-score: <strong style={{ color: crowdingColor(inst.crowding) }}>
          {inst.zScore != null ? (inst.zScore >= 0 ? '+' : '') + inst.zScore.toFixed(2) : '—'}
        </strong>
      </p>

      {loading && <p className="muted">Loading history…</p>}

      {!loading && chartData && (
        <svg width={w} height={h} style={{ display: 'block', maxWidth: '100%' }}>
          {/* zero line */}
          {chartData.zeroY >= 0 && chartData.zeroY <= h && (
            <line x1={chartData.pad} y1={chartData.zeroY}
              x2={w - chartData.pad} y2={chartData.zeroY}
              stroke="#484f58" strokeWidth={1} strokeDasharray="4,4" />
          )}
          {/* ±1σ bands */}
          {[chartData.mean + chartData.std, chartData.mean - chartData.std].map((v, i) => {
            const y = chartData.yFor(v)
            return y >= 0 && y <= h ? (
              <line key={i} x1={chartData.pad} y1={y} x2={w - chartData.pad} y2={y}
                stroke="#30363d" strokeWidth={1} strokeDasharray="2,4" />
            ) : null
          })}
          {/* ±2σ bands */}
          {[chartData.mean + 2 * chartData.std, chartData.mean - 2 * chartData.std].map((v, i) => {
            const y = chartData.yFor(v)
            return y >= 0 && y <= h ? (
              <line key={i} x1={chartData.pad} y1={y} x2={w - chartData.pad} y2={y}
                stroke="#f85149" strokeWidth={1} opacity={0.4} strokeDasharray="2,4" />
            ) : null
          })}
          {/* net line */}
          <path d={chartData.line} fill="none" stroke="#58a6ff" strokeWidth={1.5} />
          {/* latest dot */}
          {nets.length > 0 && (
            <circle
              cx={chartData.pad + (pts.length - 1) * chartData.xStep}
              cy={chartData.yFor(nets[nets.length - 1])}
              r={4} fill={crowdingColor(inst.crowding)} />
          )}
        </svg>
      )}

      {!loading && chartData && (
        <div style={{ fontSize: 11, color: '#484f58', marginTop: 4 }}>
          — — dashed = ±1σ &nbsp;|&nbsp; <span style={{ color: '#f85149', opacity: 0.6 }}>— —</span> = ±2σ extreme &nbsp;|&nbsp;
          {pts.length} weeks of data
        </div>
      )}

      {/* data table — last 12 weeks */}
      {history.length > 0 && (
        <div style={{ marginTop: 16, overflowX: 'auto' }}>
          <table className="data">
            <thead>
              <tr>
                <th>Date</th>
                <th>{inst.report === 'disagg' ? 'Mgd money net' : 'Lev net'}</th>
                <th>{inst.report === 'disagg' ? 'Mgd long' : 'Lev long'}</th>
                <th>{inst.report === 'disagg' ? 'Mgd short' : 'Lev short'}</th>
                <th>{inst.report === 'disagg' ? 'Swap dlr net' : 'Asset mgr net'}</th>
                <th>{inst.report === 'disagg' ? 'Prod/Merc net' : 'Dealer net'}</th>
                <th>Open interest</th>
              </tr>
            </thead>
            <tbody>
              {history.slice(0, 12).map((r) => {
                const dealerNet = r.dealer_long - r.dealer_short
                return (
                  <tr key={r.report_date}>
                    <td className="muted" style={{ fontSize: 12 }}>{fmtDate(r.report_date)}</td>
                    <td style={{ color: r.lev_net >= 0 ? '#d29922' : '#58a6ff', fontWeight: 600 }}>
                      {fmtK(r.lev_net)}
                    </td>
                    <td style={{ color: '#3fb950' }}>{fmtK(r.lev_long)}</td>
                    <td style={{ color: '#f85149' }}>{fmtK(r.lev_short)}</td>
                    <td style={{ color: r.asset_net >= 0 ? '#56d364' : '#ffa198' }}>
                      {fmtK(r.asset_net)}
                    </td>
                    <td className="muted">{fmtK(dealerNet)}</td>
                    <td className="muted">{fmtK(r.open_interest)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────────

export default function Cot() {
  const [snapshot, setSnapshot] = useState<CotSnapshot | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null)

  const load = async () => {
    setLoading(true); setError(null)
    try {
      const res = await api.cotSnapshot()
      setSnapshot(res)
      if (!res.hasData) setRefreshMsg('No data yet — click "Download COT data" to fetch from CFTC.')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const triggerRefresh = async () => {
    setRefreshing(true); setRefreshMsg(null)
    try {
      await api.cotRefresh(3)
      setRefreshMsg('Download started in background. This takes 30–60 seconds. Refresh the page when done.')
    } catch (e) {
      setRefreshMsg(`Error: ${(e as Error).message}`)
    } finally {
      setRefreshing(false)
    }
  }

  const selectedInst = useMemo(() =>
    snapshot?.instruments.find((i) => i.code === selected) ?? null,
    [snapshot, selected]
  )

  // Group by report type, each group sorted: has data first, then by abs z-score
  const groups = useMemo(() => {
    if (!snapshot) return []
    const sort = (arr: CotInstrument[]) =>
      [...arr].sort((a, b) => {
        if (a.hasData !== b.hasData) return a.hasData ? -1 : 1
        return Math.abs(b.zScore ?? 0) - Math.abs(a.zScore ?? 0)
      })
    const tff  = sort(snapshot.instruments.filter((i) => i.report !== 'disagg'))
    const disagg = sort(snapshot.instruments.filter((i) => i.report === 'disagg'))
    return [
      { label: 'Financial Futures (TFF Report)', items: tff },
      { label: 'Commodity Futures (Disaggregated Report)', items: disagg },
    ].filter((g) => g.items.length > 0)
  }, [snapshot])

  return (
    <div>
      <h1>Commitments of Traders (CFTC)</h1>

      {/* explainer */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <p style={{ fontSize: 14, lineHeight: 1.6, margin: 0 }}>
          The CFTC publishes positioning data every Friday at 3:30 PM ET for the prior Tuesday.
          Two reports are tracked: the <strong>TFF report</strong> (financial futures — equity index,
          rates, FX, VIX; hedge fund proxy = <em>Leveraged Money</em>) and the{' '}
          <strong>Disaggregated report</strong> (commodities — gold, oil, gas, copper;
          hedge fund proxy = <em>Managed Money</em>). When hedge fund net position hits an extreme
          z-score the trade is crowded:{' '}
          <span style={{ color: '#f85149' }}>crowded long = late to the party, reversal risk</span>;{' '}
          <span style={{ color: '#3fb950' }}>crowded short = short squeeze potential</span>.
          Watch the 1-week change: if positioning is unwinding fast, the move is already over.
        </p>
      </div>

      {/* controls */}
      <div className="controls" style={{ marginBottom: 16 }}>
        <button onClick={load} disabled={loading}>{loading ? 'Loading…' : 'Refresh page'}</button>
        <button onClick={triggerRefresh} disabled={refreshing}>
          {refreshing ? 'Starting download…' : 'Download COT data'}
        </button>
        {snapshot?.lastRefresh && (
          <span className="muted" style={{ fontSize: 12 }}>
            Last downloaded: {new Date(snapshot.lastRefresh).toLocaleString()}
          </span>
        )}
        <Tip text="CFTC releases data every Friday at 3:30 PM ET. The scheduler auto-downloads at 4:15 PM ET. Click 'Download COT data' to fetch manually — it runs in the background and takes 30-60 seconds to complete (downloads 3 years of history on first run)." />
      </div>

      {error && <div className="error-banner" style={{ marginBottom: 12 }}>{error}</div>}
      {refreshMsg && (
        <div className="stale-banner" style={{ marginBottom: 12 }}>
          {refreshMsg}
        </div>
      )}

      {!snapshot && !loading && (
        <p className="muted">Click "Download COT data" to fetch the first 3 years of history from CFTC.</p>
      )}

      {/* crowding grid — grouped by report type */}
      {groups.map((group) => (
        <div key={group.label} style={{ marginBottom: 24 }}>
          <h3 style={{ fontSize: 13, color: '#8b949e', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 10 }}>
            {group.label}
          </h3>
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
            gap: 12,
          }}>
            {group.items.map((inst) => (
              <InstrumentCard
                key={inst.code}
                inst={inst}
                selected={selected === inst.code}
                onClick={() => setSelected(selected === inst.code ? null : inst.code)}
              />
            ))}
          </div>
        </div>
      ))}

      {/* detail panel */}
      {selectedInst && <DetailPanel inst={selectedInst} />}

      {/* how to use this */}
      <div className="panel" style={{ marginTop: 16 }}>
        <h2>
          How to read this in the Crown framework
          <Tip text="This is the positioning layer. Price tells you what happened; positioning tells you who did it and whether they have room to do more." />
        </h2>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, fontSize: 13 }}>
          <div>
            <strong style={{ color: '#e6edf3' }}>Before entering a long</strong>
            <ul style={{ color: '#8b949e', lineHeight: 1.8, marginTop: 6 }}>
              <li>ES z-score &lt; +1: positioning has room to expand (bulls not crowded)</li>
              <li>Asset mgr net rising: institutional bid forming</li>
              <li>Dealer net positive: hedge funds net short → squeeze potential</li>
              <li>1-week change positive: money still flowing in, not exiting</li>
            </ul>
          </div>
          <div>
            <strong style={{ color: '#e6edf3' }}>Red flags for longs</strong>
            <ul style={{ color: '#8b949e', lineHeight: 1.8, marginTop: 6 }}>
              <li>ES z-score &gt; +2: crowded — everyone already long, no new buyers</li>
              <li>Hedge funds AND asset managers both near 52-week highs</li>
              <li>1-week change turning negative: smart money quietly reducing</li>
              <li>Open interest dropping + price rising: weak rally, buyers exhausted</li>
            </ul>
          </div>
          <div>
            <strong style={{ color: '#e6edf3' }}>Short squeeze setup</strong>
            <ul style={{ color: '#8b949e', lineHeight: 1.8, marginTop: 6 }}>
              <li>ES z-score &lt; -2: hedge funds historically max short</li>
              <li>Short % of open interest at 52-week high</li>
              <li>Technical: price holding support despite extreme bearish positioning</li>
              <li>Any positive catalyst → forced cover, sharp rally</li>
            </ul>
          </div>
          <div>
            <strong style={{ color: '#e6edf3' }}>Bond rotation signal</strong>
            <ul style={{ color: '#8b949e', lineHeight: 1.8, marginTop: 6 }}>
              <li>ZN/ZB z-score &gt; +2 (crowded long bonds): rate cut trade crowded</li>
              <li>ZN/ZB z-score &lt; -2 (crowded short bonds): squeeze = rate spike unwind</li>
              <li>Bond positioning often leads equity sector rotation by 2-3 weeks</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  )
}

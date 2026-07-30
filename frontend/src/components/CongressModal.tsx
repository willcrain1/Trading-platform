import { useEffect, useState } from 'react'
import { api, type CongressTickerDetail, type PoliticianStat } from '../api'

interface Props {
  symbol: string
  onClose: () => void
}

function fmtPct(v: number | null | undefined, sign = true): string {
  if (v == null) return '—'
  return (sign && v > 0 ? '+' : '') + v.toFixed(1) + '%'
}

function PoliticianRow({ name, stat }: { name: string; stat: PoliticianStat }) {
  const hasSellData = stat.realizedCount > 0
  const displayWr   = (hasSellData && stat.realizedWinRate != null) ? stat.realizedWinRate : stat.winRate
  const wrColor     = displayWr >= 65 ? '#3fb950' : displayWr >= 45 ? '#d29922' : '#f85149'
  const annGain     = hasSellData ? stat.avgRealizedGain : stat.avgAnnualizedGain
  const gainColor   = annGain == null ? '#8b949e' : annGain >= 0 ? '#3fb950' : '#f85149'
  const tradeCount  = hasSellData ? stat.realizedCount : stat.trades

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr auto auto auto',
      gap: '0 16px', alignItems: 'center',
      padding: '7px 10px', borderRadius: 6,
      background: '#0d1117', border: '1px solid #21262d', marginBottom: 6,
    }}>
      <span style={{ fontWeight: 600, fontSize: 13 }}>{name}</span>
      <div style={{ textAlign: 'right', minWidth: 60 }}>
        <div style={{ color: wrColor, fontWeight: 700 }}>{displayWr.toFixed(0)}%</div>
        <div className="muted" style={{ fontSize: 10 }}>{hasSellData ? 'realized win rate' : 'win rate'}</div>
      </div>
      <div style={{ textAlign: 'right', minWidth: 70 }}>
        <div style={{ color: gainColor, fontWeight: 700 }}>{fmtPct(annGain)}</div>
        <div className="muted" style={{ fontSize: 10 }}>{hasSellData ? 'avg realized gain' : 'avg annualized'}</div>
      </div>
      <div style={{ textAlign: 'right', minWidth: 40 }}>
        <div style={{ fontWeight: 700 }}>{tradeCount}</div>
        <div className="muted" style={{ fontSize: 10 }}>{hasSellData ? 'closed' : 'trades'}</div>
      </div>
    </div>
  )
}

export default function CongressModal({ symbol, onClose }: Props) {
  const [data, setData]     = useState<CongressTickerDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState<string | null>(null)

  useEffect(() => {
    api.congressTickerDetail(symbol)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [symbol])

  const ticker = data?.ticker
  const trades = ticker?.trades?.filter(tr => tr.ticker === symbol || !tr.ticker) ?? ticker?.trades ?? []

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.75)', zIndex: 200,
               display: 'flex', alignItems: 'center', justifyContent: 'center' }}
      onClick={onClose}
    >
      <div
        style={{ background: '#161b22', border: '1px solid #30363d', borderRadius: 8,
                 padding: 24, width: 620, maxWidth: '95vw', maxHeight: '80vh',
                 overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 14 }}
        onClick={e => e.stopPropagation()}
      >
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h3 style={{ margin: 0 }}>{symbol} — Congressional Buys</h3>
            {ticker && (
              <span className="muted" style={{ fontSize: 12 }}>{ticker.company}</span>
            )}
          </div>
          <button onClick={onClose} style={{ fontSize: 18, lineHeight: 1, padding: '2px 8px' }}>×</button>
        </div>

        {loading && <p className="muted">Loading...</p>}
        {error   && <p style={{ color: '#f85149' }}>{error}</p>}

        {!loading && !error && !data?.found && (
          <p className="muted" style={{ fontSize: 13 }}>
            No congressional trading data cached for <strong>{symbol}</strong>.
            Load the Politician Trades panel in the Screener first, or click Run Now in the Auto-Trade Engine to refresh.
          </p>
        )}

        {!loading && data?.found && ticker && (
          <>
            {/* summary badges */}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              {[
                { label: 'Unique politicians', value: ticker.uniquePoliticians },
                { label: 'Total buys', value: ticker.buyCount },
                {
                  label: 'Investor quality',
                  value: ticker.investorQuality ?? '—',
                  color: ticker.investorQuality === 'sharp' ? '#3fb950'
                       : ticker.investorQuality === 'mixed' ? '#d29922'
                       : ticker.investorQuality === 'weak'  ? '#f85149' : '#8b949e',
                },
                ticker.opportunity ? { label: 'Opportunity', value: ticker.opportunity } : null,
              ].filter(Boolean).map((item, i) => (
                <div key={i} style={{ background: '#0d1117', border: '1px solid #21262d',
                                      borderRadius: 6, padding: '8px 12px', textAlign: 'center', minWidth: 90 }}>
                  <div style={{ fontWeight: 700, color: (item as any).color }}>{(item as any).value}</div>
                  <div className="muted" style={{ fontSize: 10, marginTop: 2 }}>{(item as any).label}</div>
                </div>
              ))}
            </div>

            {/* politicians */}
            {ticker.politicians.length > 0 && (
              <div>
                <h4 style={{ margin: '0 0 8px' }}>Politicians buying {symbol}</h4>
                {ticker.politicians.map(pol => {
                  const stat = data.politicianStats[pol]
                  if (!stat) return (
                    <div key={pol} style={{ padding: '5px 10px', fontSize: 13, color: '#8b949e' }}>{pol}</div>
                  )
                  return <PoliticianRow key={pol} name={pol} stat={stat} />
                })}
              </div>
            )}

            {/* trade history */}
            {trades.length > 0 && (
              <div>
                <h4 style={{ margin: '0 0 8px' }}>Trade history</h4>
                <table className="data" style={{ width: '100%', fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: 'left' }}>Politician</th>
                      <th style={{ textAlign: 'left' }}>Tx date</th>
                      <th style={{ textAlign: 'left' }}>Disclosed</th>
                      <th style={{ textAlign: 'left' }}>Amount</th>
                      <th style={{ textAlign: 'right' }}>Tx→Disclosed</th>
                      <th style={{ textAlign: 'right' }}>Total return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...trades].sort((a, b) => (b.txDate || '').localeCompare(a.txDate || '')).map((tr, i) => {
                      const txDisc = tr.txToDisclosurePct
                      const total  = tr.totalReturnPct
                      const pctColor = (v: number | null | undefined) =>
                        v == null ? '#8b949e' : v > 0 ? '#3fb950' : '#f85149'
                      return (
                        <tr key={i}>
                          <td>{tr.politician}</td>
                          <td>{tr.txDate}</td>
                          <td>{tr.disclosedDate}</td>
                          <td>{tr.amount}</td>
                          <td style={{ textAlign: 'right', color: pctColor(txDisc) }}>
                            {txDisc == null ? '—' : fmtPct(txDisc)}
                          </td>
                          <td style={{ textAlign: 'right', color: pctColor(total) }}>
                            {total == null ? '—' : fmtPct(total)}
                            {tr.realized === false && <span className="muted" title="Open position, estimated"> *</span>}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
                <p className="muted" style={{ fontSize: 10, marginTop: 6 }}>
                  * Open position — total return uses today's price as estimate.
                  {data.cacheAgeSec > 0 && ` Data cached ${Math.round(data.cacheAgeSec / 60)} min ago.`}
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

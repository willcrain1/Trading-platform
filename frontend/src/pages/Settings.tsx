import { useEffect, useState } from 'react'
import { api, type BrokerConfig } from '../api'
import Tip from '../components/Tip'

type Msg = { ok: boolean; text: string }

function Card({ title, tip, children }: { title: string; tip?: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: '#161b22', border: '1px solid #30363d', borderRadius: 8,
      padding: '20px 24px', display: 'flex', flexDirection: 'column', gap: 14,
    }}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}>
        {title}{tip && <Tip text={tip} />}
      </h2>
      {children}
    </div>
  )
}

function StatusDot({ set }: { set: boolean | null }) {
  if (set === null) return null
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      background: set ? '#3fb950' : '#6e7681', marginRight: 6, flexShrink: 0,
    }} />
  )
}

function Feedback({ msg }: { msg: Msg | null }) {
  if (!msg) return null
  return (
    <div style={{
      fontSize: 13, padding: '6px 10px', borderRadius: 5,
      background: msg.ok ? 'rgba(63,185,80,0.12)' : 'rgba(248,81,73,0.12)',
      border: `1px solid ${msg.ok ? '#3fb950' : '#f85149'}44`,
      color: msg.ok ? '#3fb950' : '#f85149',
    }}>
      {msg.text}
    </div>
  )
}

function KeyField({
  label, value, onChange, placeholder, type = 'password', monospace = true,
}: {
  label: string; value: string; onChange: (v: string) => void
  placeholder?: string; type?: string; monospace?: boolean
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <label style={{ fontSize: 12, color: '#8b949e' }}>{label}</label>
      <input
        type={type}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        style={{ fontFamily: monospace ? 'monospace' : undefined, fontSize: 13 }}
      />
    </div>
  )
}

export default function Settings() {
  // ── Polygon / Massive ─────────────────────────────────────────────────────
  const [polygonSet, setPolygonSet]       = useState<boolean | null>(null)
  const [polygonKey, setPolygonKey]       = useState('')
  const [polygonSaving, setPolygonSaving] = useState(false)
  const [polygonMsg, setPolygonMsg]       = useState<Msg | null>(null)

  // ── Broker ────────────────────────────────────────────────────────────────
  const [brokerCfg, setBrokerCfg]         = useState<BrokerConfig | null>(null)
  const [broker, setBroker]               = useState<'paper' | 'alpaca'>('paper')
  const [alpacaKey, setAlpacaKey]         = useState('')
  const [alpacaSecret, setAlpacaSecret]   = useState('')
  const [alpacaPaper, setAlpacaPaper]     = useState(true)
  const [brokerSaving, setBrokerSaving]   = useState(false)
  const [brokerMsg, setBrokerMsg]         = useState<Msg | null>(null)

  // ── Analyst model ─────────────────────────────────────────────────────────
  const [analystModel, setAnalystModel]   = useState('')
  const [analystModels, setAnalystModels] = useState<string[]>([])
  const [analystSaving, setAnalystSaving] = useState(false)
  const [analystMsg, setAnalystMsg]       = useState<Msg | null>(null)

  // ── Load all config on mount ──────────────────────────────────────────────
  useEffect(() => {
    api.dataConfig().then(r => {
      setPolygonSet(r.polygonKeySet)
      setAnalystModel(r.analystModel)
      setAnalystModels(r.analystModels)
    }).catch(() => {})

    api.brokerConfig().then(cfg => {
      setBrokerCfg(cfg)
      setBroker(cfg.broker === 'alpaca' ? 'alpaca' : 'paper')
      setAlpacaPaper(cfg.alpaca?.paper ?? true)
    }).catch(() => {})
  }, [])

  // ── Polygon save / remove ─────────────────────────────────────────────────
  const savePolygon = async () => {
    if (!polygonKey.trim()) return
    setPolygonSaving(true); setPolygonMsg(null)
    try {
      await api.setPolygonKey(polygonKey.trim())
      setPolygonSet(true); setPolygonKey('')
      setPolygonMsg({ ok: true, text: 'API key saved.' })
    } catch (e) {
      setPolygonMsg({ ok: false, text: (e as Error).message })
    } finally { setPolygonSaving(false) }
  }

  const removePolygon = async () => {
    await api.deletePolygonKey()
    setPolygonSet(false)
    setPolygonMsg({ ok: true, text: 'Key removed.' })
  }

  // ── Broker save / reset ───────────────────────────────────────────────────
  const saveBroker = async () => {
    setBrokerSaving(true); setBrokerMsg(null)
    try {
      const body = broker === 'alpaca'
        ? { broker: 'alpaca' as const, alpaca: { api_key: alpacaKey, api_secret: alpacaSecret, paper: alpacaPaper } }
        : { broker: 'paper' as const }
      const res = await api.setBrokerConfig(body)
      setAlpacaKey(''); setAlpacaSecret('')
      setBrokerCfg(await api.brokerConfig())
      setBrokerMsg({ ok: true, text: `Active broker: ${res.activeLabel}` })
    } catch (e) {
      setBrokerMsg({ ok: false, text: (e as Error).message })
    } finally { setBrokerSaving(false) }
  }

  const resetBroker = async () => {
    setBrokerSaving(true); setBrokerMsg(null)
    try {
      await api.resetBrokerConfig()
      setBroker('paper'); setAlpacaKey(''); setAlpacaSecret('')
      setBrokerCfg(await api.brokerConfig())
      setBrokerMsg({ ok: true, text: 'Reset to internal paper simulator.' })
    } catch (e) {
      setBrokerMsg({ ok: false, text: (e as Error).message })
    } finally { setBrokerSaving(false) }
  }

  // ── Analyst model save ────────────────────────────────────────────────────
  const saveAnalyst = async () => {
    setAnalystSaving(true); setAnalystMsg(null)
    try {
      const res = await api.setAnalystModel(analystModel)
      setAnalystMsg({ ok: true, text: `Model set to ${res.analystModel}. Takes effect on the next trade review.` })
    } catch (e) {
      setAnalystMsg({ ok: false, text: (e as Error).message })
    } finally { setAnalystSaving(false) }
  }

  return (
    <div style={{ maxWidth: 680, margin: '0 auto', padding: '24px 16px', display: 'flex', flexDirection: 'column', gap: 16 }}>
      <h1 style={{ margin: '0 0 8px' }}>Settings</h1>

      {/* ── Price data ─────────────────────────────────────────────────── */}
      <Card
        title="Price Data — Polygon / Massive"
        tip="Polygon.io (now rebranded as Massive) provides price history as a fallback when Yahoo Finance fails — mainly for OTC tickers and stocks that were recently renamed or delisted."
      >
        <div style={{ fontSize: 13, color: '#8b949e' }}>
          <StatusDot set={polygonSet} />
          {polygonSet === null ? 'Checking…' : polygonSet ? 'API key configured' : 'No key — Yahoo Finance only'}
        </div>
        <KeyField
          label="API key (from massive.com or polygon.io)"
          value={polygonKey}
          onChange={setPolygonKey}
          placeholder="Paste your key…"
        />
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            className="primary"
            onClick={savePolygon}
            disabled={polygonSaving || !polygonKey.trim()}
          >
            {polygonSaving ? 'Saving…' : 'Save key'}
          </button>
          {polygonSet && (
            <button onClick={removePolygon} style={{ color: '#f85149' }}>Remove key</button>
          )}
        </div>
        <Feedback msg={polygonMsg} />
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>
          Key stored in <code>backend/data/app_config.json</code>. Can also be set via{' '}
          <code>POLYGON_API_KEY</code> or <code>MASSIVE_API_KEY</code> environment variable.
        </p>
      </Card>

      {/* ── Broker ─────────────────────────────────────────────────────── */}
      <Card
        title="Execution Broker"
        tip="Controls where paper trading orders are sent. The internal simulator keeps everything local. Alpaca routes orders to their API (paper or live)."
      >
        {brokerCfg && (
          <div style={{ fontSize: 13, color: '#8b949e' }}>
            <StatusDot set={true} />
            Active: <strong style={{ color: '#e6edf3' }}>{brokerCfg.activeLabel}</strong>
            {brokerCfg.broker === 'alpaca' && brokerCfg.alpaca && (
              <span>
                {' — '}{brokerCfg.alpaca.paper ? 'paper mode' : 'LIVE mode'},{' '}
                API key {brokerCfg.alpaca.api_key_set ? 'configured' : 'not set'}
              </span>
            )}
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[
            { val: 'paper' as const, label: 'Internal paper simulator (default)' },
            { val: 'alpaca' as const, label: 'Alpaca' },
          ].map(({ val, label }) => (
            <label key={val} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, cursor: 'pointer' }}>
              <input type="radio" name="broker" checked={broker === val} onChange={() => setBroker(val)} />
              {label}
            </label>
          ))}
        </div>

        {broker === 'alpaca' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, paddingLeft: 4 }}>
            <KeyField
              label="API Key"
              value={alpacaKey}
              onChange={setAlpacaKey}
              placeholder="PKXXXX…"
              type="text"
            />
            <KeyField
              label="Secret Key"
              value={alpacaSecret}
              onChange={setAlpacaSecret}
              placeholder="Secret…"
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, cursor: 'pointer' }}>
              <input type="checkbox" checked={alpacaPaper} onChange={e => setAlpacaPaper(e.target.checked)} />
              Use Alpaca paper trading (no real money)
            </label>
            {!alpacaPaper && (
              <div style={{
                background: 'rgba(248,81,73,0.12)', border: '1px solid #f85149',
                borderRadius: 6, padding: '8px 12px', fontSize: 13, color: '#f85149',
              }}>
                Live mode — real money in your Alpaca account. Make sure you understand the risks.
              </div>
            )}
            <p className="muted" style={{ fontSize: 11, margin: 0 }}>
              Get keys at alpaca.markets → Paper Trading → API Keys.
              Stored in <code>backend/data/broker_config.json</code>.
            </p>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="primary" onClick={saveBroker} disabled={brokerSaving}>
            {brokerSaving ? 'Saving…' : 'Apply'}
          </button>
          {brokerCfg?.broker !== 'paper' && (
            <button onClick={resetBroker} disabled={brokerSaving}>Reset to paper</button>
          )}
        </div>
        <Feedback msg={brokerMsg} />
      </Card>

      {/* ── Claude analyst model ────────────────────────────────────────── */}
      <Card
        title="Claude Analyst Model"
        tip="The Claude model used to review each proposed trade before execution. Opus is most thorough; Haiku is fastest. Changes take effect on the next trade review without restarting."
      >
        <div style={{ fontSize: 13, color: '#8b949e' }}>
          <StatusDot set={true} />
          Current: <strong style={{ color: '#e6edf3', fontFamily: 'monospace', fontSize: 12 }}>{analystModel || '—'}</strong>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <label style={{ fontSize: 12, color: '#8b949e' }}>Model</label>
          <select
            value={analystModel}
            onChange={e => setAnalystModel(e.target.value)}
            style={{ fontSize: 13, fontFamily: 'monospace' }}
          >
            {analystModels.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', gap: 8 }}>
          <button
            className="primary"
            onClick={saveAnalyst}
            disabled={analystSaving || !analystModel}
          >
            {analystSaving ? 'Saving…' : 'Apply'}
          </button>
        </div>
        <Feedback msg={analystMsg} />
        <p className="muted" style={{ fontSize: 11, margin: 0 }}>
          Stored in <code>backend/data/app_config.json</code>. Can be overridden via the{' '}
          <code>CLAUDE_ANALYST_MODEL</code> environment variable.
        </p>
      </Card>
    </div>
  )
}

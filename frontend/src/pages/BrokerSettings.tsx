import { useEffect, useState } from 'react'
import { api, type BrokerConfig } from '../api'
import Tip from '../components/Tip'

export default function BrokerSettings() {
  const [config, setConfig] = useState<BrokerConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  // form state
  const [broker, setBroker] = useState<'paper' | 'alpaca'>('paper')
  const [alpacaKey, setAlpacaKey] = useState('')
  const [alpacaSecret, setAlpacaSecret] = useState('')
  const [alpacaPaper, setAlpacaPaper] = useState(true)

  useEffect(() => {
    api.brokerConfig()
      .then((cfg) => {
        setConfig(cfg)
        setBroker(cfg.broker === 'alpaca' ? 'alpaca' : 'paper')
        setAlpacaPaper(cfg.alpaca?.paper ?? true)
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    setSaving(true); setError(null); setSuccess(null)
    try {
      const body =
        broker === 'alpaca'
          ? { broker: 'alpaca', alpaca: { api_key: alpacaKey, api_secret: alpacaSecret, paper: alpacaPaper } }
          : { broker: 'paper' }
      const res = await api.setBrokerConfig(body)
      setSuccess(`Active broker: ${res.activeLabel}`)
      setConfig(await api.brokerConfig())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const reset = async () => {
    setSaving(true); setError(null)
    try {
      await api.resetBrokerConfig()
      setSuccess('Reset to paper broker.')
      setBroker('paper')
      setAlpacaKey(''); setAlpacaSecret('')
      setConfig(await api.brokerConfig())
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <h1>Broker Settings</h1>

      {/* current status */}
      {config && (
        <div className="stale-banner" style={{ borderColor: '#58a6ff', color: '#58a6ff', background: 'rgba(88,166,255,0.08)', marginBottom: 16 }}>
          Active broker: <strong>{config.activeLabel}</strong>
          {config.broker === 'alpaca' && config.alpaca && (
            <span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>
              — {config.alpaca.paper ? 'paper mode' : 'LIVE mode'}, API key {config.alpaca.api_key_set ? 'configured' : 'not set'}
            </span>
          )}
        </div>
      )}

      {error && <div className="error-banner">{error}</div>}
      {success && (
        <div className="stale-banner" style={{ borderColor: '#3fb950', color: '#3fb950', background: 'rgba(63,185,80,0.08)', marginBottom: 12 }}>
          {success}
        </div>
      )}

      {loading ? (
        <p className="muted">Loading…</p>
      ) : (
        <div className="panel">
          <h2>Execution Broker</h2>
          <p style={{ fontSize: 13, color: '#8b949e', marginBottom: 16 }}>
            Controls where orders are actually sent when the paper trading engine fires.
            "Internal paper simulator" keeps everything local (no accounts needed).
            "Alpaca" routes orders to Alpaca's paper or live API.
          </p>

          <div className="controls" style={{ marginBottom: 20 }}>
            <label style={{ fontSize: 14 }}>
              <input type="radio" name="broker" value="paper"
                checked={broker === 'paper'} onChange={() => setBroker('paper')} />
              {' '}Internal paper simulator (default)
            </label>
            <label style={{ fontSize: 14 }}>
              <input type="radio" name="broker" value="alpaca"
                checked={broker === 'alpaca'} onChange={() => setBroker('alpaca')} />
              {' '}Alpaca
            </label>
          </div>

          {broker === 'alpaca' && (
            <div style={{ marginBottom: 20, display: 'grid', gap: 12, maxWidth: 460 }}>
              <div>
                <label style={{ fontSize: 13, color: '#8b949e', display: 'block', marginBottom: 4 }}>API Key</label>
                <input
                  type="text"
                  value={alpacaKey}
                  onChange={(e) => setAlpacaKey(e.target.value)}
                  placeholder="PKXXXX…"
                  style={{ width: '100%', fontFamily: 'monospace' }}
                  autoComplete="off"
                />
              </div>
              <div>
                <label style={{ fontSize: 13, color: '#8b949e', display: 'block', marginBottom: 4 }}>Secret Key</label>
                <input
                  type="password"
                  value={alpacaSecret}
                  onChange={(e) => setAlpacaSecret(e.target.value)}
                  placeholder="Secret…"
                  style={{ width: '100%', fontFamily: 'monospace' }}
                  autoComplete="off"
                />
              </div>
              <div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14 }}>
                  <input type="checkbox" checked={alpacaPaper} onChange={(e) => setAlpacaPaper(e.target.checked)} />
                  Use Alpaca paper trading (recommended — no real money)
                </label>
              </div>
              {!alpacaPaper && (
                <div style={{ background: 'rgba(248,81,73,0.12)', border: '1px solid #f85149', borderRadius: 6, padding: '8px 12px', fontSize: 13, color: '#f85149' }}>
                  Live trading is enabled. Orders will use real money in your Alpaca brokerage account.
                  Make sure you understand the risks before proceeding.
                </div>
              )}
              <p style={{ fontSize: 12, color: '#8b949e' }}>
                Get keys at alpaca.markets → Paper Trading → API Keys.
                Credentials are stored locally in backend/data/broker_config.json.
              </p>
            </div>
          )}

          <div className="controls">
            <button className="primary" onClick={save} disabled={saving}>
              {saving ? 'Saving…' : 'Apply'}
            </button>
            {config?.broker !== 'paper' && (
              <button onClick={reset} disabled={saving}>Reset to paper</button>
            )}
          </div>
        </div>
      )}

      {/* ── notes on other brokers ─────────────────────────────────────── */}
      <div className="panel" style={{ marginTop: 16 }}>
        <h2>Other brokers</h2>
        <div style={{ display: 'grid', gap: 12, fontSize: 13 }}>
          <div>
            <strong>Fidelity</strong>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              Fidelity does not provide a public API for automated retail trading.
              The closest option is to use the CSV export below to generate a trade list
              and upload it manually through Fidelity's basket/multi-trade order entry.
            </p>
          </div>
          <div>
            <strong>E*Trade / Morgan Stanley Self-Directed</strong>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              E*Trade has a developer API (OAuth, XML-based) accessible at developer.etrade.com.
              An adapter is planned — contact the project maintainer if you need this sooner.
            </p>
          </div>
          <div>
            <strong>Interactive Brokers</strong>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              IBKR's TWS API and Client Portal API are the most capable for algorithmic trading.
              An IBKR adapter is planned as a future integration.
            </p>
          </div>
          <div>
            <strong>CSV export for manual execution</strong>
            <p className="muted" style={{ margin: '4px 0 0' }}>
              The paper engine records all proposed orders. You can download them as CSV
              from the Orders table (coming soon) and upload to any broker that accepts basket orders.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

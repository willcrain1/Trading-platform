import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import TickerAnalysis from './pages/TickerAnalysis'
import MacroDashboard from './pages/MacroDashboard'
import GexView from './pages/GexView'
import RelValue from './pages/RelValue'
import Backtest from './pages/Backtest'
import PaperTrading from './pages/PaperTrading'
import TradePlans from './pages/TradePlans'
import Screener from './pages/Screener'
import Settings from './pages/Settings'
import Portfolio from './pages/Portfolio'
import Cot from './pages/Cot'

const LINKS = [
  { to: '/ticker', label: 'Ticker' },
  { to: '/macro', label: 'Macro' },
  { to: '/gex', label: 'GEX' },
  { to: '/relval', label: 'Rel Value' },
  { to: '/backtest', label: 'Backtest' },
  { to: '/screener', label: 'Screener' },
  { to: '/paper', label: 'Paper' },
  { to: '/journal', label: 'Journal' },
  { to: '/portfolio', label: 'Portfolio' },
  { to: '/cot', label: 'COT' },
  { to: '/settings', label: 'Settings' },
]

export default function App() {
  return (
    <BrowserRouter>
      <nav className="topnav">
        <span className="brand">Crown-Style Analysis</span>
        {LINKS.map((l) => (
          <NavLink key={l.to} to={l.to} className={({ isActive }) => (isActive ? 'active' : '')}>
            {l.label}
          </NavLink>
        ))}
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/ticker" replace />} />
          <Route path="/ticker" element={<TickerAnalysis />} />
          <Route path="/macro" element={<MacroDashboard />} />
          <Route path="/gex" element={<GexView />} />
          <Route path="/relval" element={<RelValue />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/screener" element={<Screener />} />
          <Route path="/paper" element={<PaperTrading />} />
          <Route path="/journal" element={<TradePlans />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/cot" element={<Cot />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/broker" element={<Navigate to="/settings" replace />} />
        </Routes>
      </main>
      <footer className="disclaimer">
        Educational analysis tool using delayed yfinance data. Not investment advice. No orders
        are placed anywhere.
      </footer>
    </BrowserRouter>
  )
}

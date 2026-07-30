"""Cross-asset macro snapshot in the Crown Macro Letter style.

Fixed watchlist pulled from yfinance; each asset gets last, daily change and a
20-day sparkline. A simple risk-on/off composite z-scores 20-day returns of
risk assets against safe-haven/stress assets.
"""
from __future__ import annotations

import numpy as np

from ..data import client

WATCHLIST = [
    {"symbol": "^GSPC", "name": "S&P 500", "group": "Equities", "risk": +1},
    {"symbol": "^IXIC", "name": "Nasdaq", "group": "Equities", "risk": +1},
    {"symbol": "^TNX", "name": "US 10Y Yield", "group": "Rates", "risk": 0},
    {"symbol": "^FVX", "name": "US 5Y Yield", "group": "Rates", "risk": 0},
    {"symbol": "^IRX", "name": "US 13W Yield", "group": "Rates", "risk": 0},
    {"symbol": "DX-Y.NYB", "name": "Dollar Index", "group": "FX", "risk": -1},
    {"symbol": "CL=F", "name": "WTI Crude", "group": "Commodities", "risk": +1},
    {"symbol": "GC=F", "name": "Gold", "group": "Commodities", "risk": -1},
    {"symbol": "HG=F", "name": "Copper", "group": "Commodities", "risk": +1},
    {"symbol": "^VIX", "name": "VIX", "group": "Volatility", "risk": -1},
    {"symbol": "BTC-USD", "name": "Bitcoin", "group": "Crypto", "risk": +1},
]


def _asset_row(meta: dict) -> dict | None:
    try:
        hist = client.get_history(meta["symbol"], period="6mo", interval="1d")
    except Exception:
        return None
    closes = [c["close"] for c in hist["candles"] if c["close"] is not None]
    if len(closes) < 21:
        return None
    last, prev = closes[-1], closes[-2]
    ret20 = (closes[-1] / closes[-21] - 1) * 100
    return {
        **meta,
        "last": round(last, 4),
        "changePct": round((last / prev - 1) * 100, 3),
        "ret20Pct": round(ret20, 3),
        "spark": [round(c, 4) for c in closes[-20:]],
        "stale": hist.get("stale", False),
    }


def dispersion_snapshot() -> dict | None:
    """VIX vs VIXEQ: index-level implied vol vs the average single-stock
    implied vol across the S&P 500 constituents.

    Index variance is roughly implied-correlation-weighted single-name
    variance, so (VIX / VIXEQ)^2 is a common back-of-envelope proxy for
    implied correlation. A wide VIXEQ-VIX spread (low proxy correlation)
    means stocks are moving on their own company-specific news — a
    dispersion/stock-picker's regime. A narrow spread (rising proxy
    correlation) means stocks are increasingly moving together on macro
    factors, which is often an early warning that a broad, correlated
    risk-off move is building.

    yfinance only exposes a live spot quote for ^VIXEQ (no usable daily
    history), so this uses get_quote for both legs instead of the
    history-based pipeline the rest of the watchlist uses.
    """
    try:
        vix = client.get_quote("^VIX")
        vixeq = client.get_quote("^VIXEQ")
    except Exception:
        return None
    v, ve = vix.get("last"), vixeq.get("last")
    if v is None or ve is None or ve <= 0:
        return None
    ratio = v / ve
    return {
        "vix": round(v, 2),
        "vixeq": round(ve, 2),
        "spread": round(ve - v, 2),
        "impliedCorrelation": round(ratio * ratio, 4),
        "stale": bool(vix.get("stale") or vixeq.get("stale")),
    }


def snapshot() -> dict:
    rows = [r for m in WATCHLIST if (r := _asset_row(m)) is not None]
    dispersion = dispersion_snapshot()

    # yield curve proxy: 10Y minus 13W (Yahoo quotes ^TNX/^IRX as yield in %)
    by_sym = {r["symbol"]: r for r in rows}
    curve = None
    if "^TNX" in by_sym and "^IRX" in by_sym:
        curve = round(by_sym["^TNX"]["last"] - by_sym["^IRX"]["last"], 3)

    # risk-on/off composite: mean of signed 20d-return z-scores.
    # z-scores are computed cross-sectionally (this basket, today) — crude but
    # keeps it self-contained with no extra history fetches.
    risk_rows = [r for r in rows if r["risk"] != 0]
    composite = None
    if len(risk_rows) >= 4:
        rets = np.array([r["ret20Pct"] for r in risk_rows])
        sd = rets.std()
        if sd > 0:
            z = (rets - rets.mean()) / sd
            composite = round(float(np.mean([zi * r["risk"] for zi, r in zip(z, risk_rows)])), 3)

    return {
        "assets": rows,
        "curve10y3m": curve,
        "riskComposite": composite,  # >0 risk-on tilt, <0 risk-off tilt
        "dispersion": dispersion,
        "stale": any(r["stale"] for r in rows) or bool(dispersion and dispersion["stale"]),
    }

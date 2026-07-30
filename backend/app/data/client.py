"""Price data client: yfinance primary, Polygon.io/Massive fallback.

All returns are JSON-serializable dicts so they can be cached as-is;
analysis modules rebuild DataFrames from them as needed.

On upstream failure a stale cache entry is served with `"stale": True`.

Delisted / renamed tickers are redirected transparently via SUCCESSOR_MAP.
When Yahoo returns no data the Polygon REST API is tried automatically,
provided a key has been configured via the UI or POLYGON_API_KEY env var.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import requests
import yfinance as yf

from . import app_config, cache

log = logging.getLogger("data.client")

TTL_DAILY    = 24 * 3600
TTL_INTRADAY = 15 * 60
TTL_OPTIONS  = 60 * 60
TTL_QUOTE    = 15 * 60

POLYGON_BASE_URL = os.environ.get("POLYGON_BASE_URL", "https://api.polygon.io")

# Tickers that no longer trade under their original symbol.
# Value = replacement ticker to use for price lookups; None = truly delisted, skip.
SUCCESSOR_MAP: dict[str, str | None] = {
    "NCR":  "VYX",   # NCR split Nov 2023 → NCR Voyix (VYX) + NCR Atleos (NATL)
    "PEAK": "DOC",   # Healthpeak merged with Physicians Realty Trust, Jan 2024
    "RE":   "EG",    # Everest Re rebranded to Everest Group, ticker → EG, 2023
    "VMW":  None,    # VMware acquired by Broadcom Nov 2023, delisted
    "TWTR": None,    # Twitter taken private Oct 2022
    "FB":   "META",  # Meta Platforms rebrand, Jun 2022
    "ATVI": None,    # Activision acquired by Microsoft, Oct 2023
    "XLNX": None,    # Acquired by AMD, Feb 2022
    "CTL":  "LUMN",  # CenturyLink rebranded to Lumen, Sep 2020
}


class TickerNotFound(Exception):
    pass


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _candles_from_df(df: pd.DataFrame) -> list[dict]:
    out = []
    for ts, row in df.iterrows():
        close = row.get("Close")
        if close is None or (isinstance(close, float) and math.isnan(close)):
            continue
        out.append(
            {
                "time":   int(ts.timestamp()),
                "open":   _clean(float(row["Open"])),
                "high":   _clean(float(row["High"])),
                "low":    _clean(float(row["Low"])),
                "close":  _clean(float(close)),
                "volume": _clean(float(row.get("Volume") or 0)),
            }
        )
    return out


_PERIOD_DAYS: dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180,
    "1y": 365, "2y": 730, "5y": 1825, "10y": 3650, "max": 3650,
}


def _fetch_polygon(symbol: str, period: str) -> dict:
    """Fetch adjusted daily OHLCV from Polygon.io / Massive as a Yahoo fallback."""
    key = app_config.get_polygon_key()
    if not key:
        raise ValueError("No Polygon/Massive API key configured")

    days     = _PERIOD_DAYS.get(period, 365)
    to_date  = datetime.now()
    from_date = to_date - timedelta(days=days)
    url = (f"{POLYGON_BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day"
           f"/{from_date.strftime('%Y-%m-%d')}/{to_date.strftime('%Y-%m-%d')}")

    resp = requests.get(url, params={"adjusted": "true", "sort": "asc",
                                     "limit": 5000, "apiKey": key}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
        raise TickerNotFound(f"{symbol} not found on Polygon (status={data.get('status')})")

    candles = [
        {
            "time":   int(r["t"]) // 1000,   # ms → s
            "open":   _clean(float(r.get("o", 0))),
            "high":   _clean(float(r.get("h", 0))),
            "low":    _clean(float(r.get("l", 0))),
            "close":  _clean(float(r.get("c", 0))),
            "volume": _clean(float(r.get("v", 0))),
        }
        for r in data["results"]
    ]
    log.info("Polygon fallback: %s returned %d candles", symbol, len(candles))
    return {"symbol": symbol, "interval": "1d", "candles": candles, "source": "polygon"}


def _fetch_or_stale(key: str, ttl: float, fetch) -> dict:
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        fresh = fetch()
    except TickerNotFound:
        raise
    except Exception:
        stale = cache.get_stale(key)
        if stale is not None:
            return {**stale, "stale": True}
        raise
    cache.set(key, fresh, ttl)
    return fresh


def get_history(symbol: str, period: str = "1y", interval: str = "1d") -> dict:
    symbol = symbol.upper().strip()

    # Redirect renamed / merged tickers before any network call
    if symbol in SUCCESSOR_MAP:
        successor = SUCCESSOR_MAP[symbol]
        if successor is None:
            raise TickerNotFound(f"{symbol} is delisted with no public successor")
        log.debug("Ticker redirect: %s → %s", symbol, successor)
        symbol = successor

    key = f"hist:{symbol}:{period}:{interval}"
    ttl = TTL_INTRADAY if interval.endswith(("m", "h")) else TTL_DAILY
    is_daily = not interval.endswith(("m", "h"))

    def fetch() -> dict:
        # ── Primary: Yahoo Finance ────────────────────────────────────────────
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
            if not df.empty:
                return {"symbol": symbol, "interval": interval, "candles": _candles_from_df(df)}
        except Exception as exc:
            log.debug("Yahoo failed for %s: %s", symbol, exc)

        # ── Fallback: Polygon / Massive (daily bars only) ─────────────────────
        if is_daily:
            try:
                return _fetch_polygon(symbol, period)
            except Exception as exc:
                log.debug("Polygon fallback failed for %s: %s", symbol, exc)

        raise TickerNotFound(f"{symbol}: no price data from Yahoo or Polygon (period={period})")

    return _fetch_or_stale(key, ttl, fetch)


def get_quote(symbol: str) -> dict:
    """Last price + previous close via fast_info, cached briefly."""
    symbol = symbol.upper().strip()
    key = f"quote:{symbol}"

    def fetch() -> dict:
        fi = yf.Ticker(symbol).fast_info
        last = fi.get("lastPrice")
        if last is None:
            raise TickerNotFound(symbol)
        return {
            "symbol": symbol,
            "last": _clean(float(last)),
            "previousClose": _clean(fi.get("previousClose") and float(fi["previousClose"])),
            "currency": fi.get("currency"),
        }

    return _fetch_or_stale(key, TTL_QUOTE, fetch)


def get_name(symbol: str) -> str:
    """Company/fund short name, cached for 24h. Returns the symbol itself on failure."""
    symbol = symbol.upper().strip()
    key = f"name:{symbol}"

    def fetch() -> dict:
        info = yf.Ticker(symbol).info
        name = info.get("shortName") or info.get("longName") or symbol
        return {"name": name}

    try:
        return _fetch_or_stale(key, TTL_DAILY, fetch)["name"]
    except Exception:
        return symbol


def get_option_expirations(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    key = f"optexp:{symbol}"

    def fetch() -> dict:
        dates = yf.Ticker(symbol).options
        return {"symbol": symbol, "expirations": list(dates)}

    return _fetch_or_stale(key, TTL_OPTIONS, fetch)


def get_option_chain(symbol: str, expiration: str) -> dict:
    symbol = symbol.upper().strip()
    key = f"chain:{symbol}:{expiration}"

    def fetch() -> dict:
        chain = yf.Ticker(symbol).option_chain(expiration)

        def records(df: pd.DataFrame) -> list[dict]:
            cols = ["strike", "openInterest", "impliedVolatility", "lastPrice", "volume"]
            sub = df[[c for c in cols if c in df.columns]]
            return [
                {k: _clean(v) for k, v in rec.items()}
                for rec in sub.to_dict(orient="records")
            ]

        return {
            "symbol": symbol,
            "expiration": expiration,
            "calls": records(chain.calls),
            "puts": records(chain.puts),
        }

    return _fetch_or_stale(key, TTL_OPTIONS, fetch)

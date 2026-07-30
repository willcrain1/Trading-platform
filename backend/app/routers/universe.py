"""Automated universe selection via sector rotation.

Ranks the 11 SPDR sector ETFs by a blended momentum score
(40 % 1-month return + 60 % 3-month return).  The top-N sectors'
holdings become the candidate universe fed to the signal scanner.

Endpoints
─────────
GET  /api/universe?topN=2&refresh=0   – ranked sectors + candidate tickers
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter

from ..analysis import indicators
from ..data import client
from .scan import _score

router = APIRouter(prefix="/api/universe", tags=["universe"])

# ── static sector map ─────────────────────────────────────────────────────────

SECTOR_NAMES: dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLY":  "Consumer Disc",
    "XLI":  "Industrials",
    "XLB":  "Materials",
    "XLU":  "Utilities",
    "XLRE": "Real Estate",
    "XLC":  "Comm Services",
    "XLP":  "Consumer Staples",
}

# Top 12 liquid holdings per sector (static — refreshed manually as needed).
# Order matters: holdings are added to the universe in this order when sectors
# are selected, so the most-liquid names come first.
SECTOR_HOLDINGS: dict[str, list[str]] = {
    "XLK":  ["AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CSCO", "AMD", "QCOM", "TXN", "INTU", "NOW", "ADBE"],
    "XLF":  ["JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK", "AXP", "C", "SCHW", "PGR"],
    "XLE":  ["XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "VLO", "OXY", "DVN", "HAL", "BKR"],
    "XLV":  ["LLY", "UNH", "ABBV", "JNJ", "MRK", "TMO", "ABT", "DHR", "AMGN", "BMY", "SYK", "ISRG"],
    "XLY":  ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG", "CMG", "ORLY", "GM"],
    "XLI":  ["GE", "RTX", "HON", "UPS", "BA", "CAT", "DE", "LMT", "UNP", "ETN", "MMM", "ITW"],
    "XLB":  ["LIN", "SHW", "APD", "FCX", "NEM", "ECL", "PPG", "NUE", "ALB", "VMC", "MLM", "IP"],
    "XLU":  ["NEE", "DUK", "SO", "D", "AEP", "EXC", "XEL", "PCG", "ED", "ETR", "ES", "AWK"],
    "XLRE": ["PLD", "AMT", "EQIX", "CCI", "PSA", "O", "WELL", "DLR", "AVB", "EQR", "SPG", "VICI"],
    "XLC":  ["GOOGL", "META", "NFLX", "TMUS", "CHTR", "DIS", "CMCSA", "VZ", "T", "EA", "TTWO", "LYV"],
    "XLP":  ["WMT", "PG", "COST", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "GIS", "STZ", "KMB"],
}

SECTOR_ETFS = list(SECTOR_NAMES.keys())
MAX_WORKERS = 6
CACHE_TTL = 4 * 3600  # refresh at most every 4 hours

_cache: dict = {"built_at": 0.0, "sectors": None}


# ── sector scoring ────────────────────────────────────────────────────────────

def _score_sector(etf: str) -> dict:
    try:
        hist = client.get_history(etf, period="1y")
        df = indicators.to_df(hist["candles"])
        close = df["close"]
        ret20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 21 else None
        ret60 = float((close.iloc[-1] / close.iloc[-61] - 1) * 100) if len(close) >= 61 else None
        momentum = round(0.4 * (ret20 or 0) + 0.6 * (ret60 or 0), 2)
        sig = indicators.compute_signals(hist["candles"])
        return {
            "etf": etf,
            "name": SECTOR_NAMES[etf],
            "last": round(float(close.iloc[-1]), 2),
            "ret20": round(ret20, 2) if ret20 is not None else None,
            "ret60": round(ret60, 2) if ret60 is not None else None,
            "momentum": momentum,
            "score": _score(sig),
            "trend": sig.get("trend"),
            "stale": hist.get("stale", False),
            "holdings": SECTOR_HOLDINGS[etf],
            "error": None,
        }
    except Exception as e:
        return {
            "etf": etf,
            "name": SECTOR_NAMES[etf],
            "last": None,
            "ret20": None,
            "ret60": None,
            "momentum": -999.0,
            "score": -5,
            "trend": None,
            "stale": False,
            "holdings": SECTOR_HOLDINGS[etf],
            "error": str(e),
        }


def _build_sectors() -> list[dict]:
    sectors: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_score_sector, etf): etf for etf in SECTOR_ETFS}
        for fut in as_completed(futures):
            sectors.append(fut.result())
    sectors.sort(key=lambda s: s["momentum"], reverse=True)
    for i, s in enumerate(sectors):
        s["rank"] = i + 1
    return sectors


# ── endpoint ──────────────────────────────────────────────────────────────────

@router.get("")
def universe(topN: int = 2, refresh: int = 0):
    global _cache
    now = time.time()
    if refresh or _cache["sectors"] is None or (now - _cache["built_at"]) > CACHE_TTL:
        _cache["sectors"] = _build_sectors()
        _cache["built_at"] = now

    sectors = _cache["sectors"]

    # Collect holdings from top-N sectors, deduplicating across sectors
    seen: set[str] = set()
    universe_tickers: list[str] = []
    for s in sectors:
        if s["rank"] <= topN:
            for ticker in s["holdings"]:
                if ticker not in seen:
                    seen.add(ticker)
                    universe_tickers.append(ticker)

    result_sectors = [{**s, "selected": s["rank"] <= topN} for s in sectors]

    return {
        "sectors": result_sectors,
        "universe": universe_tickers,
        "topN": topN,
        "builtAt": _cache["built_at"],
    }

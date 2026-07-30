"""Watchlist screener + persistent signal tracker.

One-shot scan: POST /api/scan with a list of tickers, returns scored results.

Signal tracker: a server-side watchlist (scan_watchlist table) is scanned
automatically twice daily by the scheduler.  Results are stored in scan_history
so you can surface tickers that have held a high score for N consecutive scans —
a sustained signal is a much stronger setup than a single-day reading.

Endpoints
─────────
GET  /api/scan/presets          – curated preset universes
POST /api/scan                  – ad-hoc scan of up to 50 tickers
GET  /api/scan/watchlist        – persistent auto-scan watchlist
POST /api/scan/watchlist        – add symbols to watchlist
DEL  /api/scan/watchlist/{sym}  – remove a symbol
POST /api/scan/run-now          – manually trigger a watchlist scan
GET  /api/scan/sustained        – tickers with unbroken score streak ≥ N
GET  /api/scan/history/{sym}    – raw score history for one symbol
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import groupby
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..analysis import indicators
from ..data import client

router = APIRouter(prefix="/api/scan", tags=["scan"])

# ── presets ───────────────────────────────────────────────────────────────────

PRESETS: dict[str, list[str]] = {
    "macro": ["SPY", "QQQ", "IWM", "TLT", "GLD", "HYG", "UUP", "VXX", "XLF", "XLE"],
    "sectors": ["XLK", "XLF", "XLE", "XLV", "XLY", "XLI", "XLB", "XLU", "XLRE", "XLC", "XLP"],
    "megacap": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "JPM", "V"],
    "rates": ["TLT", "IEF", "SHY", "HYG", "LQD", "MBB", "TIP", "EMB"],
}

MAX_WORKERS = 6
MAX_TICKERS = 50
HISTORY_DAYS = 90  # how long to keep scan_history rows


# ── database ──────────────────────────────────────────────────────────────────

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "paper.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_watchlist (
  symbol   TEXT PRIMARY KEY,
  added_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol            TEXT NOT NULL,
  scanned_at        REAL NOT NULL,
  score             INTEGER NOT NULL,
  trend             TEXT,
  rsi               REAL,
  macd_above_signal INTEGER,
  recent_cross      TEXT,
  atr_pct           REAL,
  last_price        REAL,
  stale             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_scan_hist_sym ON scan_history(symbol, scanned_at);
CREATE TABLE IF NOT EXISTS smart_buy_alerts (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker                 TEXT NOT NULL,
  detected_at            REAL NOT NULL,
  score_at_detection     INTEGER NOT NULL,
  investor_quality       TEXT NOT NULL,
  investor_quality_score REAL,
  avg_annualized_gain    REAL,
  max_ann_gain           REAL,
  politicians            TEXT NOT NULL,
  buy_count              INTEGER NOT NULL DEFAULT 0,
  opportunity            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sba_ticker ON smart_buy_alerts(ticker, detected_at);
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        for col, typedef in [
            ("avg_annualized_gain", "REAL"),
            ("max_ann_gain",        "REAL"),
        ]:
            try:
                _conn.execute(f"ALTER TABLE smart_buy_alerts ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # already exists
        _conn.commit()
    return _conn


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _db_rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return _rows(_get_conn().execute(sql, params))


# ── scoring ───────────────────────────────────────────────────────────────────

def _score(sig: dict) -> int:
    """Multi-factor bullish alignment score: −5 (full bear) … +5 (full bull)."""
    s = 0
    trend = sig.get("trend")
    if trend == "uptrend":
        s += 2
    elif trend == "downtrend":
        s -= 2
    cross = sig.get("recentCross")
    if cross == "golden_cross":
        s += 1
    elif cross == "death_cross":
        s -= 1
    if sig.get("macdAboveSignal") is True:
        s += 1
    elif sig.get("macdAboveSignal") is False:
        s -= 1
    r = sig.get("rsi")
    if r is not None:
        if r < 30:
            s += 1
        elif r > 70:
            s -= 1
    return s


def _scan_one(symbol: str) -> dict:
    try:
        hist = client.get_history(symbol, period="2y")
        sig = indicators.compute_signals(hist["candles"])
        return {
            "symbol": symbol,
            "stale": hist.get("stale", False),
            "score": _score(sig),
            **sig,
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "score": 0}


# ── scheduled scan (called by scheduler.py) ───────────────────────────────────

def run_scheduled_scan() -> dict:
    """Scan every symbol in scan_watchlist and append to scan_history."""
    symbols = [r["symbol"] for r in _db_rows("SELECT symbol FROM scan_watchlist")]
    if not symbols:
        return {"scanned": 0, "errors": []}

    scanned_at = time.time()
    to_insert: list[tuple] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_scan_one, sym): sym for sym in symbols}
        for fut in as_completed(futures):
            r = fut.result()
            if r.get("error"):
                errors.append(f"{r['symbol']}: {r['error']}")
                continue
            macd_flag = (
                1 if r.get("macdAboveSignal") is True
                else 0 if r.get("macdAboveSignal") is False
                else None
            )
            to_insert.append((
                r["symbol"], scanned_at, r["score"],
                r.get("trend"), r.get("rsi"), macd_flag,
                r.get("recentCross"), r.get("atrPct"), r.get("last"),
                1 if r.get("stale") else 0,
            ))

    with _lock:
        conn = _get_conn()
        if to_insert:
            conn.executemany(
                "INSERT INTO scan_history "
                "(symbol, scanned_at, score, trend, rsi, macd_above_signal, "
                " recent_cross, atr_pct, last_price, stale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                to_insert,
            )
        # Trim old rows
        conn.execute(
            "DELETE FROM scan_history WHERE scanned_at < ?",
            (time.time() - HISTORY_DAYS * 86400,),
        )
        conn.commit()

    return {"scanned": len(to_insert), "errors": errors}


# ── smart buy persistence ─────────────────────────────────────────────────────

def record_smart_buys(alerts: list[dict]) -> int:
    """Persist detected smart-buy opportunities and auto-add them to the watchlist.

    Skips tickers that were already recorded today to avoid duplicate rows.
    Returns the number of new rows inserted.
    """
    if not alerts:
        return 0
    day_start = time.time() - 86400
    with _lock:
        conn = _get_conn()
        existing = {
            r[0] for r in conn.execute(
                "SELECT ticker FROM smart_buy_alerts WHERE detected_at > ?",
                (day_start,),
            ).fetchall()
        }
        now = time.time()
        to_insert = [a for a in alerts if a.get("ticker") not in existing]
        if to_insert:
            conn.executemany(
                "INSERT INTO smart_buy_alerts "
                "(ticker, detected_at, score_at_detection, investor_quality, "
                " investor_quality_score, avg_annualized_gain, max_ann_gain, "
                " politicians, buy_count, opportunity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        a["ticker"], now,
                        a.get("score") or 0,
                        a.get("investorQuality", "unknown"),
                        a.get("investorQualityScore"),
                        a.get("avgAnnualizedGain"),
                        a.get("maxAnnualizedGain"),
                        json.dumps(a.get("politicians", [])),
                        a.get("buyCount", 0),
                        a.get("opportunity"),
                    )
                    for a in to_insert
                ],
            )
            # Auto-add new smart-buy tickers to the watchlist for ongoing score tracking
            conn.executemany(
                "INSERT OR IGNORE INTO scan_watchlist (symbol, added_at) VALUES (?, ?)",
                [(a["ticker"], now) for a in to_insert],
            )
            conn.commit()
    return len(to_insert)


def _assess(score_at_detection: int, history: list[dict]) -> str:
    """Derive a plain-English buy assessment from the score trajectory."""
    if not history:
        return "new"
    current_score = history[0]["score"]  # history is sorted newest-first
    delta = current_score - score_at_detection
    if current_score >= 2:
        return "confirmed"
    if delta >= 2:
        return "improving"
    if delta <= -2:
        return "deteriorating"
    # Stale: detected more than 14 days ago with no meaningful change
    if len(history) >= 14 and abs(delta) < 2:
        return "stale"
    return "accumulate"


# ── API endpoints ─────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    tickers: list[str] = Field(min_length=1, max_length=MAX_TICKERS)


class WatchlistAddRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)


@router.get("/presets")
def presets():
    return {"presets": PRESETS}


@router.post("")
def scan(req: ScanRequest):
    tickers = list({t.upper() for t in req.tickers})[:MAX_TICKERS]
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_scan_one, t): t for t in tickers}
        for fut in as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda r: (-r.get("score", 0), r.get("symbol", "")))
    return {"results": results, "count": len(results)}


# ── watchlist CRUD ────────────────────────────────────────────────────────────

@router.get("/watchlist")
def get_watchlist():
    rows = _db_rows("SELECT symbol, added_at FROM scan_watchlist ORDER BY added_at")
    return {"symbols": [{"symbol": r["symbol"], "addedAt": r["added_at"]} for r in rows]}


@router.post("/watchlist")
def add_to_watchlist(req: WatchlistAddRequest):
    now = time.time()
    symbols = [s.strip().upper() for s in req.symbols if s.strip()]
    with _lock:
        conn = _get_conn()
        conn.executemany(
            "INSERT OR IGNORE INTO scan_watchlist (symbol, added_at) VALUES (?, ?)",
            [(s, now) for s in symbols],
        )
        conn.commit()
    return {"added": len(symbols)}


@router.delete("/watchlist/{symbol}")
def remove_from_watchlist(symbol: str):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM scan_watchlist WHERE symbol = ?", (symbol.upper(),))
        conn.commit()
    return {"ok": True}


@router.post("/run-now")
def run_now():
    """Manually trigger a watchlist scan outside the scheduler."""
    result = run_scheduled_scan()
    return result


# ── history & sustained signals ───────────────────────────────────────────────

@router.get("/history/{symbol}")
def history(symbol: str, days: int = 30):
    cutoff = time.time() - days * 86400
    rows = _db_rows(
        "SELECT scanned_at, score, trend, rsi, macd_above_signal "
        "FROM scan_history WHERE symbol = ? AND scanned_at > ? "
        "ORDER BY scanned_at DESC LIMIT 100",
        (symbol.upper(), cutoff),
    )
    return {
        "symbol": symbol.upper(),
        "history": [
            {
                "ts": r["scanned_at"],
                "score": r["score"],
                "trend": r["trend"],
                "rsi": r["rsi"],
                "macdAboveSignal": (
                    True if r["macd_above_signal"] == 1
                    else False if r["macd_above_signal"] == 0
                    else None
                ),
            }
            for r in rows
        ],
    }


@router.get("/sustained")
def sustained(minScore: int = 2, minScans: int = 3, days: int = 90):
    """Tickers whose most recent consecutive scans all had score >= minScore.

    minScore can be negative to surface bearish streaks (score <= minScore).
    Returns results ranked by streak length, then score.
    """
    cutoff = time.time() - days * 86400
    rows = _db_rows(
        "SELECT symbol, score, scanned_at, trend, rsi, macd_above_signal, "
        "recent_cross, atr_pct, last_price, stale "
        "FROM scan_history WHERE scanned_at > ? "
        "ORDER BY symbol, scanned_at DESC",
        (cutoff,),
    )

    results: list[dict] = []
    bearish = minScore < 0

    for symbol, grp in groupby(rows, key=lambda r: r["symbol"]):
        scans = list(grp)  # already DESC by scanned_at
        streak = 0
        for scan in scans:
            passes = scan["score"] <= minScore if bearish else scan["score"] >= minScore
            if passes:
                streak += 1
            else:
                break
        if streak < minScans:
            continue
        latest = scans[0]
        results.append({
            "symbol": symbol,
            "streak": streak,
            "score": latest["score"],
            "trend": latest["trend"],
            "rsi": latest["rsi"],
            "macdAboveSignal": (
                True if latest["macd_above_signal"] == 1
                else False if latest["macd_above_signal"] == 0
                else None
            ),
            "recentCross": latest["recent_cross"],
            "atrPct": latest["atr_pct"],
            "lastPrice": latest["last_price"],
            "stale": bool(latest["stale"]),
            "lastSeen": latest["scanned_at"],
            "history": [
                {"ts": s["scanned_at"], "score": s["score"]}
                for s in scans[:30]
            ],
        })

    results.sort(key=lambda r: (-r["streak"], -r["score"] if not bearish else r["score"]))
    return {"results": results, "count": len(results)}


@router.get("/auto-select/status")
def auto_select_status():
    """Return the last auto-selection result and scheduled job info."""
    from ..trading.auto_selector import get_last_result
    from ..trading.scheduler import job_info
    return {
        "lastResult": get_last_result() or None,
        "scheduledJobs": [j for j in job_info() if "auto_select" in j["id"]],
    }


@router.post("/auto-select/run-now")
def auto_select_run_now():
    """Manually trigger the auto-selector outside the scheduler."""
    from ..trading.auto_selector import run_auto_selection
    return run_auto_selection("manual")


@router.get("/smart-buys")
def smart_buys(days: int = 30):
    """Smart Buy Monitor — contrarian tickers detected at 10am/4pm scans.

    Returns each unique ticker's latest detection enriched with score trajectory
    and a buy assessment based on how the technical score has moved since detection.
    """
    cutoff = time.time() - days * 86400
    # One row per ticker: most recent detection
    alert_rows = _db_rows(
        "SELECT ticker, MAX(detected_at) AS detected_at, score_at_detection, "
        "investor_quality, investor_quality_score, politicians, buy_count, opportunity "
        "FROM smart_buy_alerts WHERE detected_at > ? "
        "GROUP BY ticker ORDER BY detected_at DESC",
        (cutoff,),
    )

    results = []
    for row in alert_rows:
        ticker = row["ticker"]
        # Pull recent score history from scan_history (newest first)
        hist = _db_rows(
            "SELECT score, scanned_at FROM scan_history "
            "WHERE symbol = ? AND scanned_at >= ? "
            "ORDER BY scanned_at DESC LIMIT 30",
            (ticker, row["detected_at"]),
        )
        assessment = _assess(row["score_at_detection"], hist)
        politicians = json.loads(row["politicians"] or "[]")
        results.append({
            "ticker":               ticker,
            "detectedAt":           row["detected_at"],
            "scoreAtDetection":     row["score_at_detection"],
            "currentScore":         hist[0]["score"] if hist else None,
            "scoreTrend":           [h["score"] for h in reversed(hist)],
            "investorQuality":      row["investor_quality"],
            "investorQualityScore": row["investor_quality_score"],
            "politicians":          politicians,
            "buyCount":             row["buy_count"],
            "opportunity":          row["opportunity"],
            "assessment":           assessment,
        })

    return {"alerts": results, "count": len(results), "daysBack": days}

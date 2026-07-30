"""Fidelity portfolio import and analysis.

Fidelity has no public trading API, but lets you export positions as CSV
from the Positions page. This router accepts that CSV (or manual entry),
persists the holdings, and runs the same signal analysis as the screener
so you can see trend/RSI/MACD/score against your actual portfolio.

Parsing notes:
- The Fidelity CSV has boilerplate header lines before the real column header.
  We scan until we find a row containing "Symbol".
- Numbers come in with $, commas, and % — all stripped before float conversion.
- The last N rows are account totals and disclaimer text — we stop when we
  hit a row where the symbol cell is empty or says "Account Total".
- Options positions (very long symbols) are kept in the DB but flagged as
  unscannable — yfinance won't find them.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..analysis import indicators
from ..data import client
from ..routers.scan import _score

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "paper.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  description TEXT,
  qty REAL,
  cost_per_share REAL,
  cost_total REAL,
  source TEXT NOT NULL DEFAULT 'fidelity',
  imported_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS position_tiers (
  symbol TEXT PRIMARY KEY,
  tier TEXT NOT NULL CHECK(tier IN ('core', 'tactical', 'trade'))
);
"""


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        # migrate: add source column to existing installs that predate it
        try:
            _conn.execute("ALTER TABLE portfolio_positions ADD COLUMN source TEXT DEFAULT 'fidelity'")
            _conn.commit()
        except Exception:
            pass  # column already exists
    return _conn


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ── CSV parser ────────────────────────────────────────────────────────────────

def _clean_num(v: str) -> float | None:
    if not v:
        return None
    v = re.sub(r"[$,%+\s]", "", v).replace(",", "").strip()
    if v in ("-", "--", "n/a", "N/A", ""):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def parse_fidelity_csv(content: str) -> list[dict]:
    """Parse a Fidelity "Download" positions CSV export.

    Returns a list of position dicts with keys:
      symbol, description, qty, cost_per_share, cost_total
    Rows that look like totals/disclaimers are dropped.
    """
    # normalise line endings
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    reader = csv.reader(io.StringIO(content))
    rows = [r for r in reader]

    # find the data header row — must contain "Symbol" (case-insensitive)
    header_idx = None
    for i, row in enumerate(rows):
        if any("symbol" in cell.lower() for cell in row):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Could not find a header row containing 'Symbol'. "
            "Please use Fidelity's Positions CSV export."
        )

    # normalise header names
    raw_headers = [h.strip().strip('"').lower() for h in rows[header_idx]]

    def _col(*candidates: str) -> int | None:
        for name in candidates:
            for i, h in enumerate(raw_headers):
                if name in h:
                    return i
        return None

    sym_col = _col("symbol", "ticker")
    desc_col = _col("description", "name")
    qty_col = _col("quantity", "qty", "shares")
    cpu_col = _col("cost basis per share", "cost/share", "avg cost", "average cost")
    cpt_col = _col("cost basis total", "total cost basis", "cost basis")
    # "cost basis total" before "cost basis" so the more-specific match wins
    if cpu_col is not None and cpt_col == cpu_col:
        cpt_col = None

    if sym_col is None:
        raise ValueError("No 'Symbol' column found in the CSV header.")

    def _safe(row: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip().strip('"')

    positions: list[dict] = []
    for row in rows[header_idx + 1:]:
        if not row or all(c.strip() == "" for c in row):
            continue
        symbol = _safe(row, sym_col)
        if not symbol:
            continue
        # stop at totals / disclaimer lines
        if re.match(r"(account\s*total|total|pending|disclaimer)", symbol, re.I):
            break
        # skip obvious non-ticker cells
        if len(symbol) > 20 or " " in symbol:
            continue

        positions.append({
            "symbol": symbol.upper(),
            "description": _safe(row, desc_col) or None,
            "qty": _clean_num(_safe(row, qty_col)),
            "cost_per_share": _clean_num(_safe(row, cpu_col)),
            "cost_total": _clean_num(_safe(row, cpt_col)),
        })

    if not positions:
        raise ValueError("No valid positions found in the CSV.")
    return positions


# ── storage helpers ───────────────────────────────────────────────────────────

def _save_positions(positions: list[dict], source: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM portfolio_positions WHERE source=?", (source,))
        now = time.time()
        for p in positions:
            conn.execute(
                "INSERT INTO portfolio_positions"
                " (symbol, description, qty, cost_per_share, cost_total, source, imported_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (p["symbol"], p.get("description"), p.get("qty"),
                 p.get("cost_per_share"), p.get("cost_total"), source, now),
            )
        conn.commit()


def _load_positions() -> list[dict]:
    with _lock:
        return _rows(_get_conn().execute("SELECT * FROM portfolio_positions ORDER BY id"))


def _load_tiers() -> dict[str, str]:
    with _lock:
        rows = _get_conn().execute("SELECT symbol, tier FROM position_tiers").fetchall()
        return {r["symbol"]: r["tier"] for r in rows}


def _set_tier(symbol: str, tier: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO position_tiers (symbol, tier) VALUES (?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET tier=excluded.tier",
            (symbol, tier),
        )
        conn.commit()


def _delete_tier(symbol: str) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM position_tiers WHERE symbol=?", (symbol,))
        conn.commit()


# ── signal analysis ───────────────────────────────────────────────────────────

_OPTION_RE = re.compile(r"^[A-Z]+\d{6}[CP]\d+$")  # e.g. AAPL241220C00100000


def _exit_plan(candles: list[dict], current_price: float, qty: float | None) -> dict | None:
    """ATR-based exit levels: 2×ATR stop, 2:1 take-profit, trailing stop %."""
    try:
        df = indicators.to_df(candles)
        atr_series = indicators.atr(df)
        atr_val = float(atr_series.dropna().iloc[-1]) if not atr_series.dropna().empty else None
        if not atr_val or atr_val <= 0 or not current_price:
            return None
        stop = round(current_price - 2 * atr_val, 2)
        stop_pct = round((stop / current_price - 1) * 100, 1)
        risk = current_price - stop
        tp = round(current_price + 2 * risk, 2)
        tp_pct = round((tp / current_price - 1) * 100, 1)
        trail_pct = round(risk / current_price * 100, 1)
        dollar_risk = round(-risk * qty, 2) if qty else None
        return {
            "stopLoss": stop,
            "stopLossPct": stop_pct,
            "takeProfit": tp,
            "takeProfitPct": tp_pct,
            "trailingStopPct": trail_pct,
            "dollarRiskIfStopped": dollar_risk,
            "atr": round(atr_val, 2),
        }
    except Exception:
        return None


def _analyze_one(pos: dict) -> dict:
    symbol = pos["symbol"]
    base = {
        "id": pos["id"],
        "symbol": symbol,
        "description": pos.get("description"),
        "qty": pos.get("qty"),
        "costPerShare": pos.get("cost_per_share"),
        "costTotal": pos.get("cost_total"),
        "source": pos.get("source", "fidelity"),
    }
    # options: skip signal analysis
    if _OPTION_RE.match(symbol):
        return {**base, "assetType": "option", "score": 0}

    try:
        quote = client.get_quote(symbol)
        current_price = quote.get("last")
    except Exception:
        current_price = None

    if current_price and pos.get("qty"):
        current_value = round(current_price * pos["qty"], 2)
        cost = pos.get("cost_total") or (
            pos["cost_per_share"] * pos["qty"] if pos.get("cost_per_share") else None
        )
        unrealized_pnl = round(current_value - cost, 2) if cost else None
        unrealized_pnl_pct = round((current_value / cost - 1) * 100, 2) if cost and cost > 0 else None
    else:
        current_value = None
        unrealized_pnl = None
        unrealized_pnl_pct = None

    try:
        hist = client.get_history(symbol, period="2y")
        sig = indicators.compute_signals(hist["candles"])
        stale = hist.get("stale", False)
    except Exception as e:
        return {**base, "currentPrice": current_price, "currentValue": current_value,
                "unrealizedPnl": unrealized_pnl, "unrealizedPnlPct": unrealized_pnl_pct,
                "error": str(e), "score": 0}

    ep = _exit_plan(hist["candles"], current_price, pos.get("qty")) if current_price else None

    return {
        **base,
        "currentPrice": current_price,
        "currentValue": current_value,
        "unrealizedPnl": unrealized_pnl,
        "unrealizedPnlPct": unrealized_pnl_pct,
        "stale": stale,
        "score": _score(sig),
        "exitPlan": ep,
        **sig,
    }


def _analyze_all(positions: list[dict]) -> list[dict]:
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_analyze_one, p): p for p in positions}
        for fut in as_completed(futures):
            results.append(fut.result())
    tiers = _load_tiers()
    for r in results:
        r["tier"] = tiers.get(r["symbol"])
    results.sort(key=lambda r: (-r.get("score", 0), r.get("symbol", "")))
    return results


# ── endpoints ─────────────────────────────────────────────────────────────────

class CsvImportRequest(BaseModel):
    csv: str = Field(min_length=10)


class ManualPosition(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    qty: float | None = None
    costPerShare: float | None = None


class ManualImportRequest(BaseModel):
    positions: list[ManualPosition] = Field(min_length=1, max_length=200)


@router.post("/import-csv")
def import_csv(req: CsvImportRequest):
    try:
        parsed = parse_fidelity_csv(req.csv)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save_positions(parsed, source="fidelity")
    results = _analyze_all(_load_positions())
    return {"imported": len(parsed), "results": results}


@router.post("/import-manual")
def import_manual(req: ManualImportRequest):
    positions = [
        {
            "symbol": p.symbol.upper().strip(),
            "description": None,
            "qty": p.qty,
            "cost_per_share": p.costPerShare,
            "cost_total": (p.qty * p.costPerShare) if (p.qty and p.costPerShare) else None,
        }
        for p in req.positions
    ]
    _save_positions(positions, source="manual")
    results = _analyze_all(_load_positions())
    return {"imported": len(positions), "results": results}


@router.get("/positions")
def positions():
    return {"positions": _load_positions()}


@router.post("/analyze")
def analyze():
    stored = _load_positions()
    if not stored:
        raise HTTPException(400, "No positions imported. Use /import-csv or /import-manual first.")
    return {"results": _analyze_all(stored)}


@router.delete("")
def clear(source: str | None = None):
    with _lock:
        conn = _get_conn()
        if source:
            conn.execute("DELETE FROM portfolio_positions WHERE source=?", (source,))
        else:
            conn.execute("DELETE FROM portfolio_positions")
        conn.commit()
    return {"ok": True}


class TierRequest(BaseModel):
    symbol: str
    tier: str


@router.get("/tags")
def get_tags():
    """Return all tier tags keyed by symbol."""
    return {"tags": _load_tiers()}


@router.post("/tags")
def set_tag(req: TierRequest):
    """Set or update the tier for a symbol."""
    if req.tier not in ("core", "tactical", "trade"):
        raise HTTPException(400, "tier must be core, tactical, or trade")
    _set_tier(req.symbol.upper(), req.tier)
    return {"ok": True}


@router.delete("/tags/{symbol}")
def delete_tag(symbol: str):
    """Remove the tier tag from a symbol."""
    _delete_tier(symbol.upper())
    return {"ok": True}

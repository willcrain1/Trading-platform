"""Congress STOCK Act trade disclosure data.

Primary source: Capitol Trades (capitoltrades.com) — scraped via HTML/RSC payload.
Fallback: CSV import — user pastes or uploads a CSV.
Legacy: Quiver Quantitative API (requires paid Tier 1, kept for config compatibility).

All trades (scraped and CSV-imported) are persisted to data/congress.db so they
survive restarts and accumulate history across refreshes. A small meta table
tracks which source is active and when it was last refreshed (6h TTL).
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from . import app_config

# ── Capitol Trades scraper ─────────────────────────────────────────────────────

CT_BASE    = "https://www.capitoltrades.com/trades"
CT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
PAGE_SIZE  = 96
CACHE_TTL  = 6 * 3600

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "congress.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_SKIP = {"", "--", "N/A", "NA", "SPX", "NDX", "RUT", "VIX",
         "NONE", "CASH", "N/A)", "COMP", "INDU"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS congress_trades (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_type            TEXT NOT NULL,
  source             TEXT NOT NULL,
  ticker             TEXT NOT NULL,
  politician         TEXT NOT NULL,
  chamber            TEXT,
  party              TEXT,
  tx_date            TEXT,
  disclosed_date     TEXT,
  amount             TEXT,
  asset_description  TEXT,
  district           TEXT,
  UNIQUE(tx_type, source, ticker, politician, tx_date, disclosed_date, amount)
);
CREATE INDEX IF NOT EXISTS idx_congress_ticker     ON congress_trades(ticker);
CREATE INDEX IF NOT EXISTS idx_congress_pol_ticker ON congress_trades(politician, ticker);
CREATE INDEX IF NOT EXISTS idx_congress_txdate     ON congress_trades(tx_date);
CREATE TABLE IF NOT EXISTS congress_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
"""


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in _get_conn().execute(sql, params).fetchall()]


def _meta_get(key: str) -> str | None:
    rows = _rows("SELECT value FROM congress_meta WHERE key=?", (key,))
    return rows[0]["value"] if rows else None


def _meta_set(key: str, value: str) -> None:
    with _lock:
        _get_conn().execute(
            "INSERT OR REPLACE INTO congress_meta(key,value) VALUES(?,?)", (key, value)
        )
        _get_conn().commit()


def _upsert_trades_nolock(conn: sqlite3.Connection, rows: list[dict], tx_type: str, source: str) -> int:
    added = 0
    for r in rows:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO congress_trades "
                "(tx_type, source, ticker, politician, chamber, party, tx_date, "
                " disclosed_date, amount, asset_description, district) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tx_type, source, r["ticker"], r["politician"], r["chamber"], r["party"],
                 r["txDate"], r["disclosedDate"], r["amount"], r["assetDescription"], r["district"]),
            )
            added += conn.execute("SELECT changes()").fetchone()[0]
        except Exception:
            pass
    return added


def _upsert_trades(rows: list[dict], tx_type: str, source: str) -> int:
    if not rows:
        return 0
    with _lock:
        conn = _get_conn()
        added = _upsert_trades_nolock(conn, rows, tx_type, source)
        conn.commit()
    return added


def _has_source(source: str) -> bool:
    rows = _rows("SELECT COUNT(*) as n FROM congress_trades WHERE source=?", (source,))
    return rows[0]["n"] > 0 if rows else False


def _row_to_trade(r: dict) -> dict:
    return {
        "ticker":           r["ticker"],
        "politician":       r["politician"],
        "chamber":          r["chamber"] or "unknown",
        "party":            r["party"] or "Unknown",
        "txDate":           r["tx_date"] or "",
        "disclosedDate":    r["disclosed_date"] or "",
        "amount":           r["amount"] or "",
        "assetDescription": r["asset_description"] or "",
        "district":         r["district"] or "",
    }


# ── HTML / RSC scraping ────────────────────────────────────────────────────────

def _fetch_page(page: int) -> list[dict]:
    """Fetch one page of trades from Capitol Trades and return raw trade dicts."""
    url = f"{CT_BASE}?pageSize={PAGE_SIZE}&page={page}"
    resp = requests.get(url, headers=CT_HEADERS, timeout=30)
    resp.raise_for_status()
    html = resp.text

    # RSC payloads: self.__next_f.push([1,"<js-escaped-string>"])
    scripts = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)

    # Find the block that contains trade data (largest block with txDate)
    data_script = next(
        (s for s in sorted(scripts, key=len, reverse=True)
         if "txDate" in s and len(s) > 10_000),
        None,
    )
    if not data_script:
        return []

    # Decode the JS string literal (handles \" \\ \n \uXXXX etc.)
    try:
        decoded: str = json.loads('"' + data_script + '"')
    except json.JSONDecodeError:
        decoded = data_script.replace('\\"', '"').replace("\\\\", "\\")

    # Extract the "data":[{...}] array using bracket balancing
    marker = '"data":['
    start = decoded.find(marker)
    if start == -1:
        return []
    arr_start = start + len(marker) - 1  # position of '['
    depth = 0
    end = arr_start
    for i, ch in enumerate(decoded[arr_start:], arr_start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        return json.loads(decoded[arr_start:end])
    except json.JSONDecodeError:
        return []


def _raw_to_normalized(raw: dict) -> dict | None:
    """Convert a Capitol Trades raw buy dict to our normalized format."""
    tx_type = (raw.get("txType") or "").lower()
    if tx_type not in ("buy", "purchase"):
        return None

    issuer = raw.get("issuer") or {}
    ticker_raw = (issuer.get("issuerTicker") or "").split(":")[0].strip().upper()
    ticker_raw = ticker_raw.lstrip("$").replace("/", "-")  # $NCR→NCR, BRK/B→BRK-B
    if not ticker_raw or ticker_raw in _SKIP or len(ticker_raw) > 6:
        return None
    # Allow only letters and hyphens (BRK-B style); reject anything else
    if not all(c.isalpha() or c == "-" for c in ticker_raw):
        return None

    pol = raw.get("politician") or {}
    first = pol.get("firstName") or ""
    last  = pol.get("lastName") or ""
    name  = f"{first} {last}".strip()

    chamber = (raw.get("chamber") or pol.get("chamber") or "").lower()
    if chamber not in ("house", "senate"):
        chamber = "unknown"

    party_raw = (pol.get("party") or "").lower()
    if "republican" in party_raw or party_raw == "r":
        party = "Republican"
    elif "democrat" in party_raw or party_raw == "d":
        party = "Democrat"
    else:
        party = party_raw.title() or "Unknown"

    # pubDate is the disclosure/report date; txDate is when the trade happened
    pub_date = (raw.get("pubDate") or "")[:10]  # "2026-07-10T..." -> "2026-07-10"
    tx_date  = raw.get("txDate") or ""

    value = raw.get("value") or 0
    amount = _value_to_range(value)

    return {
        "ticker":           ticker_raw,
        "politician":       name,
        "chamber":          chamber,
        "party":            party,
        "txDate":           tx_date,
        "disclosedDate":    pub_date,
        "amount":           amount,
        "assetDescription": issuer.get("issuerName") or "",
        "district":         pol.get("_stateId") or "",
    }


def _raw_to_sell(raw: dict) -> dict | None:
    """Normalize a Capitol Trades sell/sale record."""
    tx_type = (raw.get("txType") or "").lower()
    if tx_type not in ("sell", "sale", "sold"):
        return None

    issuer = raw.get("issuer") or {}
    ticker_raw = (issuer.get("issuerTicker") or "").split(":")[0].strip().upper()
    ticker_raw = ticker_raw.lstrip("$").replace("/", "-")
    if not ticker_raw or ticker_raw in _SKIP or len(ticker_raw) > 6:
        return None
    if not all(c.isalpha() or c == "-" for c in ticker_raw):
        return None

    pol = raw.get("politician") or {}
    name = f"{pol.get('firstName') or ''} {pol.get('lastName') or ''}".strip()

    chamber = (raw.get("chamber") or pol.get("chamber") or "").lower()
    if chamber not in ("house", "senate"):
        chamber = "unknown"

    party_raw = (pol.get("party") or "").lower()
    if "republican" in party_raw or party_raw == "r":
        party = "Republican"
    elif "democrat" in party_raw or party_raw == "d":
        party = "Democrat"
    else:
        party = party_raw.title() or "Unknown"

    pub_date = (raw.get("pubDate") or "")[:10]
    tx_date  = raw.get("txDate") or ""
    value = raw.get("value") or 0

    return {
        "ticker":           ticker_raw,
        "politician":       name,
        "chamber":          chamber,
        "party":            party,
        "txDate":           tx_date,
        "disclosedDate":    pub_date,
        "amount":           _value_to_range(value),
        "assetDescription": issuer.get("issuerName") or "",
        "district":         pol.get("_stateId") or "",
    }


def _value_to_range(value: int) -> str:
    if not value:
        return "Unknown"
    if value < 1_001:
        return "$1–$1K"
    if value < 15_001:
        return "$1K–$15K"
    if value < 50_001:
        return "$15K–$50K"
    if value < 100_001:
        return "$50K–$100K"
    if value < 250_001:
        return "$100K–$250K"
    if value < 500_001:
        return "$250K–$500K"
    if value < 1_000_001:
        return "$500K–$1M"
    return "$1M+"


def scrape(days_back: int = 90, max_pages: int = 30) -> dict:
    """Scrape Capitol Trades for recent buy/sell transactions and upsert into the DB."""
    cutoff   = datetime.now() - timedelta(days=days_back)
    all_raw  : list[dict] = []
    errors   : list[str]  = []

    for page in range(1, max_pages + 1):
        try:
            rows = _fetch_page(page)
        except Exception as e:
            errors.append(f"Page {page}: {e}")
            break

        if not rows:
            break

        all_raw.extend(rows)

        # Stop when all remaining trades are older than cutoff
        last_tx = rows[-1].get("txDate") or rows[-1].get("pubDate", "")[:10]
        try:
            last_dt = datetime.strptime(last_tx[:10], "%Y-%m-%d")
        except ValueError:
            last_dt = datetime.now()
        if last_dt < cutoff:
            break

        time.sleep(0.3)  # polite crawl delay

    # Convert to internal format; split buys and sells
    buys: list[dict] = []
    sells: list[dict] = []
    for raw in all_raw:
        b = _raw_to_normalized(raw)
        if b:
            buys.append(b)
            continue
        s = _raw_to_sell(raw)
        if s:
            sells.append(s)

    _upsert_trades(buys, "buy", "scrape")
    _upsert_trades(sells, "sell", "scrape")
    _meta_set("scrape_errors", json.dumps(errors))
    _meta_set("fetched_at", str(time.time()))
    _meta_set("active_source", "scrape")
    return cache_info()


def refresh() -> dict:
    """Refresh trade data: try scraping first, fall back to CSV import."""
    has_csv = _has_source("csv")

    # CSV import takes priority if it exists (user explicitly imported) and no key is set
    if has_csv and not app_config.get_quiver_key():
        _meta_set("active_source", "csv")
        _meta_set("fetched_at", str(time.time()))
        _meta_set("scrape_errors", "[]")
        return cache_info()

    # Try Capitol Trades scrape
    try:
        return scrape()
    except Exception as e:
        errors = [f"Scrape failed: {e}"]
        if has_csv:
            _meta_set("active_source", "csv")
            _meta_set("fetched_at", str(time.time()))
        _meta_set("scrape_errors", json.dumps(errors))
        return cache_info()


def _ensure_fresh() -> None:
    fetched_at = float(_meta_get("fetched_at") or 0)
    active_source = _meta_get("active_source")
    if (time.time() - fetched_at) > CACHE_TTL or not active_source:
        refresh()


def reset_cache() -> None:
    """Force the next _ensure_fresh() call to re-fetch (used after config changes)."""
    _meta_set("fetched_at", "0")


# ── CSV import ─────────────────────────────────────────────────────────────────

_COL_ALIASES = {
    "ticker":        ["ticker", "symbol", "asset", "stock"],
    "politician":    ["politician", "name", "member", "representative", "senator", "rep", "official"],
    "transaction":   ["transaction", "type", "trade_type", "action", "transaction_type"],
    "txDate":        ["date", "transaction_date", "tx_date", "traded_on", "traded", "purchase_date",
                      "transaction date", "traded on"],
    "disclosedDate": ["report_date", "reportdate", "disclosed", "disclosure_date", "filed",
                      "filed_date", "published", "disclosure date", "report date", "published_date"],
    "amount":        ["range", "amount", "value", "transaction_amount", "size", "amount_range",
                      "amount range"],
    "chamber":       ["house", "chamber", "body"],
    "party":         ["party", "political_party"],
}


def _map_headers(headers: list[str]) -> dict[str, int]:
    lower = [h.lower().strip().replace(" ", "_") for h in headers]
    result: dict[str, int] = {}
    for field, aliases in _COL_ALIASES.items():
        for alias in aliases:
            alias_norm = alias.replace(" ", "_")
            for i, h in enumerate(lower):
                if h == alias_norm or h.replace("-", "_") == alias_norm:
                    result[field] = i
                    break
            if field in result:
                break
    return result


def _is_buy(val: str) -> bool:
    v = val.lower().strip()
    return any(k in v for k in ("purchase", "buy", "bought", "acquisition"))


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """Parse CSV text into normalized buy trade rows. Returns (rows, errors)."""
    errors: list[str] = []
    rows:   list[dict] = []
    try:
        reader = csv.reader(io.StringIO(text.strip()))
        headers_raw = next(reader)
    except StopIteration:
        return [], ["CSV is empty"]
    except Exception as e:
        return [], [f"Failed to parse CSV: {e}"]

    mapping = _map_headers(headers_raw)
    if "ticker" not in mapping:
        errors.append(f"Could not find a ticker/symbol column. Found: {headers_raw[:8]}")
        return [], errors

    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        def get(field: str) -> str:
            idx = mapping.get(field)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        ticker = get("ticker").upper().replace("$", "").strip()
        if not ticker or ticker in _SKIP or len(ticker) > 5:
            continue
        tx_type = get("transaction") or "purchase"
        if not _is_buy(tx_type):
            continue
        chamber_raw = get("chamber").lower()
        if "senate" in chamber_raw:
            chamber = "senate"
        elif "house" in chamber_raw or "representative" in chamber_raw:
            chamber = "house"
        else:
            chamber = "unknown"

        tx_dt   = _parse_date(get("txDate"))
        disc_dt = _parse_date(get("disclosedDate"))

        rows.append({
            "ticker":           ticker,
            "politician":       get("politician"),
            "chamber":          chamber,
            "party":            get("party") or "Unknown",
            "txDate":           tx_dt.strftime("%Y-%m-%d") if tx_dt else get("txDate"),
            "disclosedDate":    disc_dt.strftime("%Y-%m-%d") if disc_dt else get("disclosedDate"),
            "amount":           get("amount") or "Unknown",
            "assetDescription": "",
            "district":         "",
        })
    return rows, errors


def import_csv(text: str) -> dict:
    rows, errors = parse_csv(text)
    if not rows:
        return {"ok": False,
                "error": errors[0] if errors else "No buy trades found in CSV",
                "rowsParsed": 0, "warnings": errors[1:]}
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM congress_trades WHERE source='csv'")
        _upsert_trades_nolock(conn, rows, "buy", "csv")
        conn.commit()
    _meta_set("active_source", "csv")
    _meta_set("fetched_at", str(time.time()))
    _meta_set("scrape_errors", "[]")
    return {"ok": True, "rowsParsed": len(rows), "warnings": errors}


def clear_imported() -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM congress_trades WHERE source='csv'")
        conn.commit()
    _meta_set("active_source", "")
    _meta_set("fetched_at", "0")


def has_import() -> bool:
    return _has_source("csv")


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip()[:10], fmt)
        except ValueError:
            pass
    return None


# ── public API ────────────────────────────────────────────────────────────────

def get_recent_buys(days_back: int = 90, chamber: str = "both") -> list[dict]:
    _ensure_fresh()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    source = _meta_get("active_source") or "scrape"
    sql = (
        "SELECT ticker, politician, chamber, party, tx_date, disclosed_date, amount, "
        "asset_description, district FROM congress_trades "
        "WHERE tx_type='buy' AND source=? AND "
        "(CASE WHEN tx_date != '' THEN tx_date ELSE disclosed_date END) >= ?"
    )
    params: list = [source, cutoff]
    if chamber != "both":
        sql += " AND chamber=?"
        params.append(chamber)
    return [_row_to_trade(r) for r in _rows(sql, tuple(params))]


def get_top_tickers(days_back: int = 90, min_buys: int = 1,
                    chamber: str = "both") -> list[dict]:
    trades = get_recent_buys(days_back=days_back, chamber=chamber)
    agg: dict[str, dict] = {}
    for t in trades:
        tk = t["ticker"]
        if tk not in agg:
            agg[tk] = {"ticker": tk, "buyCount": 0, "politicians": set(), "trades": [], "company": ""}
        agg[tk]["buyCount"] += 1
        agg[tk]["politicians"].add(t["politician"])
        if len(agg[tk]["trades"]) < 10:
            agg[tk]["trades"].append(t)
        if not agg[tk]["company"]:
            agg[tk]["company"] = t.get("assetDescription") or ""

    result = [
        {
            "ticker":            v["ticker"],
            "company":           v["company"],
            "buyCount":          v["buyCount"],
            "uniquePoliticians": len(v["politicians"]),
            "politicians":       sorted(v["politicians"]),
            "trades":            sorted(v["trades"], key=lambda x: x["txDate"], reverse=True),
        }
        for v in agg.values()
        if v["buyCount"] >= min_buys
    ]
    result.sort(key=lambda x: (-x["uniquePoliticians"], -x["buyCount"]))
    return result


def get_recent_sells(days_back: int = 90) -> list[dict]:
    """Return all sell transactions within the window, newest first."""
    _ensure_fresh()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    source = _meta_get("active_source") or "scrape"
    rows = _rows(
        "SELECT ticker, politician, chamber, party, tx_date, disclosed_date, amount, "
        "asset_description, district FROM congress_trades "
        "WHERE tx_type='sell' AND source=? AND tx_date >= ? "
        "ORDER BY tx_date DESC",
        (source, cutoff),
    )
    return [_row_to_trade(r) for r in rows]


def get_sells_by_pol_ticker(days_back: int = 365) -> dict[tuple[str, str], list[str]]:
    """Return {(politician, ticker): [sell_txDates ascending]} for closed-position matching.

    Uses a wider window (default 365d) so buys near the edge of the buys window can
    still be matched against sells that arrived a few months later.
    """
    _ensure_fresh()
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    source = _meta_get("active_source") or "scrape"
    rows = _rows(
        "SELECT politician, ticker, tx_date FROM congress_trades "
        "WHERE tx_type='sell' AND source=? AND tx_date >= ? "
        "ORDER BY tx_date ASC",
        (source, cutoff),
    )
    result: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        result.setdefault((r["politician"], r["ticker"]), []).append(r["tx_date"])
    return result


def get_debug_sample(limit: int = 3) -> list[dict]:
    _ensure_fresh()
    source = _meta_get("active_source") or "scrape"
    rows = _rows(
        "SELECT tx_type, ticker, politician, chamber, party, tx_date, disclosed_date, "
        "amount, asset_description, district FROM congress_trades "
        "WHERE source=? ORDER BY id DESC LIMIT ?",
        (source, limit),
    )
    return rows


def cache_info() -> dict:
    active_source = _meta_get("active_source") or "none"
    fetched_at = float(_meta_get("fetched_at") or 0)
    errors = json.loads(_meta_get("scrape_errors") or "[]")
    if active_source != "none":
        total_trades = _rows(
            "SELECT COUNT(*) as n FROM congress_trades WHERE tx_type='buy' AND source=?",
            (active_source,),
        )[0]["n"]
        total_sells = _rows(
            "SELECT COUNT(*) as n FROM congress_trades WHERE tx_type='sell' AND source=?",
            (active_source,),
        )[0]["n"]
    else:
        total_trades = total_sells = 0
    return {
        "fetchedAt":   fetched_at,
        "totalTrades": total_trades,
        "totalSells":  total_sells,
        "errors":      errors,
        "hasKey":      bool(app_config.get_quiver_key()),
        "source":      active_source,
        "hasImport":   has_import(),
    }

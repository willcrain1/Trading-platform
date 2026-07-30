"""CFTC Commitments of Traders — two reports:

  Traders in Financial Futures (TFF) — equity index, rates, FX, VIX futures
    Annual:  https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip
    Current: https://www.cftc.gov/dea/newcot/FinFutWk.txt
    Hedge fund proxy: Lev_Money_Positions_Long/Short_All

  Disaggregated — commodity futures (gold, oil, nat gas, etc.)
    Annual:  https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip
    Current: https://www.cftc.gov/dea/newcot/fut_disagg_txt.zip
    Hedge fund proxy: M_Money_Positions_Long/Short_All

Released every Friday 3:30 PM ET for the prior Tuesday's positions.

Key signal: hedge-fund net position z-score vs 52-week history.
  z > +2  → crowded long  — trade is full, reversal risk elevated
  z < -2  → crowded short — squeeze potential for shorts
"""
from __future__ import annotations

import io
import logging
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("cot")

_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "cot.db"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

ANNUAL_URL         = "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
CURRENT_URL        = "https://www.cftc.gov/dea/newcot/FinFutWk.txt"
DISAGG_ANNUAL_URL  = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
# Current-week disagg file is a plain text CSV (with header), not a ZIP
DISAGG_CURRENT_URL = "https://www.cftc.gov/dea/newcot/c_disagg.txt"
HEADERS = {"User-Agent": "Mozilla/5.0 (TradingPlatform research tool; non-commercial)"}
TIMEOUT = 45

# TFF report — financial futures (equity index, rates, FX, VIX).
# Hedge fund proxy: Lev_Money_Positions_Long/Short_All
INSTRUMENTS: list[dict] = [
    {"code": "ES",  "label": "S&P 500 E-mini",     "etf": "SPY",  "report": "tff",
     "name_substr": ["E-MINI S&P 500", "S&P 500 MINI"]},
    {"code": "NQ",  "label": "Nasdaq-100 E-mini",   "etf": "QQQ",  "report": "tff",
     "name_substr": ["NASDAQ-100 MINI", "NASDAQ-100 STOCK", "E-MINI NASDAQ"]},
    {"code": "RTY", "label": "Russell 2000 E-mini",  "etf": "IWM",  "report": "tff",
     "name_substr": ["E-MINI RUSSELL 2000", "RUSSELL 2000 MINI"]},
    {"code": "ZN",  "label": "10-Year T-Note",       "etf": "IEF",  "report": "tff",
     "name_substr": ["UST 10Y NOTE"]},
    {"code": "ZB",  "label": "30-Year T-Bond",       "etf": "TLT",  "report": "tff",
     "name_substr": ["UST BOND - CHICAGO BOARD"]},
    {"code": "EUR", "label": "Euro FX",              "etf": "FXE",  "report": "tff",
     "name_substr": ["EURO FX"]},
    {"code": "JPY", "label": "Japanese Yen",         "etf": "FXY",  "report": "tff",
     "name_substr": ["JAPANESE YEN"]},
    {"code": "VIX", "label": "VIX Futures",          "etf": "VXX",  "report": "tff",
     "name_substr": ["CBOE VOLATILITY INDEX", "VOLATILITY INDEX", "CBOE S&P 500 VOL",
                     "VIX FUTURES", "S&P 500 VIX"]},
]

# Disaggregated report — commodity futures.
# Hedge fund proxy: M_Money_Positions_Long/Short_All
DISAGG_INSTRUMENTS: list[dict] = [
    {"code": "GC", "label": "Gold",        "etf": "GLD",  "report": "disagg",
     "name_substr": ["GOLD - COMMODITY EXCHANGE", "GOLD-COMMODITY EXCH"]},
    {"code": "SI", "label": "Silver",      "etf": "SLV",  "report": "disagg",
     "name_substr": ["SILVER - COMMODITY EXCHANGE", "SILVER-COMMODITY EXCH"]},
    {"code": "CL", "label": "WTI Crude",   "etf": "USO",  "report": "disagg",
     "name_substr": ["CRUDE OIL, LIGHT SWEET", "LIGHT SWEET CRUDE OIL"]},
    {"code": "NG", "label": "Natural Gas", "etf": "UNG",  "report": "disagg",
     "name_substr": ["NATURAL GAS - NEW YORK MERCANTILE", "NAT GAS"]},
    {"code": "HG", "label": "Copper",      "etf": "CPER", "report": "disagg",
     "name_substr": ["COPPER-GRADE #1", "COPPER- GRADE #1", "COPPER GRADE #1",
                     "HIGH GRADE COPPER", "COPPER - COMMODITY"]},
]

ALL_INSTRUMENTS = INSTRUMENTS + DISAGG_INSTRUMENTS
CODE_SET = {i["code"] for i in ALL_INSTRUMENTS}

# Shared columns
_DATE_COL = "Report_Date_as_YYYY-MM-DD"
_NAME_COL = "Market_and_Exchange_Names"
_OI_COL   = "Open_Interest_All"

# TFF report column names ("All" = futures + options combined)
_LEV_LONG     = "Lev_Money_Positions_Long_All"
_LEV_SHORT    = "Lev_Money_Positions_Short_All"
_ASSET_LONG   = "Asset_Mgr_Positions_Long_All"
_ASSET_SHORT  = "Asset_Mgr_Positions_Short_All"
_DEALER_LONG  = "Dealer_Positions_Long_All"
_DEALER_SHORT = "Dealer_Positions_Short_All"

# Disaggregated report column names
# M_Money = Managed Money (hedge fund equivalent)
# Prod_Merc = Producer/Merchant (commercial hedgers)
# Swap = Swap Dealers — CFTC uses double underscore: Swap__Positions_*_All (known quirk)
_MM_LONG    = "M_Money_Positions_Long_All"
_MM_SHORT   = "M_Money_Positions_Short_All"
_PROD_LONG  = "Prod_Merc_Positions_Long_All"
_PROD_SHORT = "Prod_Merc_Positions_Short_All"
# Swap columns: try double-underscore first (actual CFTC format), fall back to single
_SWAP_LONG_CANDIDATES  = ["Swap__Positions_Long_All",  "Swap_Positions_Long_All"]
_SWAP_SHORT_CANDIDATES = ["Swap__Positions_Short_All", "Swap_Positions_Short_All"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cot_history (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  code          TEXT    NOT NULL,
  report_date   TEXT    NOT NULL,
  lev_long      REAL,
  lev_short     REAL,
  lev_net       REAL,
  asset_long    REAL,
  asset_short   REAL,
  asset_net     REAL,
  dealer_long   REAL,
  dealer_short  REAL,
  open_interest REAL,
  UNIQUE(code, report_date)
);
CREATE INDEX IF NOT EXISTS idx_cot ON cot_history(code, report_date);
CREATE TABLE IF NOT EXISTS cot_meta (
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
    rows = _rows("SELECT value FROM cot_meta WHERE key=?", (key,))
    return rows[0]["value"] if rows else None


def _meta_set(key: str, value: str) -> None:
    with _lock:
        _get_conn().execute(
            "INSERT OR REPLACE INTO cot_meta(key,value) VALUES(?,?)", (key, value)
        )
        _get_conn().commit()


# ── download helpers ──────────────────────────────────────────────────────────

_DATE_COLS_ALT = [
    "Report_Date_as_YYYY-MM-DD",   # annual ZIP format
    "As_of_Date_In_Form_YYMMDD",   # current-week file format
    "As_of_Date_in_Form_YYYY-MM-DD",  # older format variant
]


def _parse_csv_bytes(data: bytes) -> pd.DataFrame:
    """Parse a CFTC CSV/text blob into a DataFrame. Tries multiple delimiters."""
    for sep in (",", "|", "\t", ";"):
        try:
            df = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, low_memory=False)
            df.columns = [c.strip() for c in df.columns]
            has_name = _NAME_COL in df.columns
            has_date = any(c in df.columns for c in _DATE_COLS_ALT)
            if has_name and has_date:
                return df
        except Exception:
            continue
    raise ValueError("Could not parse CFTC data — unexpected format")


def _download_annual(year: int) -> pd.DataFrame | None:
    """Download and parse one year's TFF ZIP."""
    url = ANNUAL_URL.format(year=year)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.warning("COT annual download failed for %d: %s", year, e)
        return None

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        # Find the CSV inside — could be any .txt or .csv file
        names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".csv"))]
        if not names:
            log.warning("COT ZIP for %d contains no text files: %s", year, zf.namelist())
            return None
        data = zf.read(names[0])
        return _parse_csv_bytes(data)
    except Exception as e:
        log.warning("COT ZIP parse failed for %d: %s", year, e)
        return None


# Column order for the headerless current-week file (same schema as annual FinFutYY.txt)
_CURRENT_WEEK_COLS = [
    "Market_and_Exchange_Names", "As_of_Date_In_Form_YYMMDD", "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code", "CFTC_Market_Code", "CFTC_Region_Code", "CFTC_Commodity_Code",
    "Open_Interest_All",
    "Dealer_Positions_Long_All", "Dealer_Positions_Short_All", "Dealer_Positions_Spread_All",
    "Asset_Mgr_Positions_Long_All", "Asset_Mgr_Positions_Short_All", "Asset_Mgr_Positions_Spread_All",
    "Lev_Money_Positions_Long_All", "Lev_Money_Positions_Short_All", "Lev_Money_Positions_Spread_All",
    "Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All", "Other_Rept_Positions_Spread_All",
    "Tot_Rept_Positions_Long_All", "Tot_Rept_Positions_Short_All",
    "NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
    "Change_in_Open_Interest_All",
    "Change_in_Dealer_Long_All", "Change_in_Dealer_Short_All", "Change_in_Dealer_Spread_All",
    "Change_in_Asset_Mgr_Long_All", "Change_in_Asset_Mgr_Short_All", "Change_in_Asset_Mgr_Spread_All",
    "Change_in_Lev_Money_Long_All", "Change_in_Lev_Money_Short_All", "Change_in_Lev_Money_Spread_All",
    "Change_in_Other_Rept_Long_All", "Change_in_Other_Rept_Short_All", "Change_in_Other_Rept_Spread_All",
    "Change_in_Tot_Rept_Long_All", "Change_in_Tot_Rept_Short_All",
    "Change_in_NonRept_Long_All", "Change_in_NonRept_Short_All",
    "Pct_of_Open_Interest_All",
    "Pct_of_OI_Dealer_Long_All", "Pct_of_OI_Dealer_Short_All", "Pct_of_OI_Dealer_Spread_All",
    "Pct_of_OI_Asset_Mgr_Long_All", "Pct_of_OI_Asset_Mgr_Short_All", "Pct_of_OI_Asset_Mgr_Spread_All",
    "Pct_of_OI_Lev_Money_Long_All", "Pct_of_OI_Lev_Money_Short_All", "Pct_of_OI_Lev_Money_Spread_All",
    "Pct_of_OI_Other_Rept_Long_All", "Pct_of_OI_Other_Rept_Short_All", "Pct_of_OI_Other_Rept_Spread_All",
    "Pct_of_OI_Tot_Rept_Long_All", "Pct_of_OI_Tot_Rept_Short_All",
    "Pct_of_OI_NonRept_Long_All", "Pct_of_OI_NonRept_Short_All",
    "Traders_Tot_All",
    "Traders_Dealer_Long_All", "Traders_Dealer_Short_All", "Traders_Dealer_Spread_All",
    "Traders_Asset_Mgr_Long_All", "Traders_Asset_Mgr_Short_All", "Traders_Asset_Mgr_Spread_All",
    "Traders_Lev_Money_Long_All", "Traders_Lev_Money_Short_All", "Traders_Lev_Money_Spread_All",
    "Traders_Other_Rept_Long_All", "Traders_Other_Rept_Short_All", "Traders_Other_Rept_Spread_All",
    "Traders_Tot_Rept_Long_All", "Traders_Tot_Rept_Short_All",
    "Conc_Gross_LE_4_TDR_Long_All", "Conc_Gross_LE_4_TDR_Short_All",
    "Conc_Gross_LE_8_TDR_Long_All", "Conc_Gross_LE_8_TDR_Short_All",
    "Conc_Net_LE_4_TDR_Long_All", "Conc_Net_LE_4_TDR_Short_All",
    "Conc_Net_LE_8_TDR_Long_All", "Conc_Net_LE_8_TDR_Short_All",
    "Contract_Units",
    "CFTC_Contract_Market_Code_Quotes", "CFTC_Market_Code_Quotes", "CFTC_Commodity_Code_Quotes",
    "CFTC_SubGroup_Code", "FutOnly_or_Combined",
]

# Column order for the headerless current-week Disaggregated file (c_disagg.txt).
# Same schema as annual disagg TXT file. Note: Swap uses double underscore (CFTC quirk).
_DISAGG_CURRENT_WEEK_COLS = [
    "Market_and_Exchange_Names", "As_of_Date_In_Form_YYMMDD", "Report_Date_as_YYYY-MM-DD",
    "CFTC_Contract_Market_Code", "CFTC_Market_Code", "CFTC_Region_Code", "CFTC_Commodity_Code",
    "Open_Interest_All",
    "Prod_Merc_Positions_Long_All", "Prod_Merc_Positions_Short_All",
    "Swap__Positions_Long_All", "Swap__Positions_Short_All", "Swap__Positions_Spread_All",
    "M_Money_Positions_Long_All", "M_Money_Positions_Short_All", "M_Money_Positions_Spread_All",
    "Other_Rept_Positions_Long_All", "Other_Rept_Positions_Short_All", "Other_Rept_Positions_Spread_All",
    "Tot_Rept_Positions_Long_All", "Tot_Rept_Positions_Short_All",
    "NonRept_Positions_Long_All", "NonRept_Positions_Short_All",
    "Change_in_Open_Interest_All",
    "Change_in_Prod_Merc_Long_All", "Change_in_Prod_Merc_Short_All",
    "Change_in_Swap_Long_All", "Change_in_Swap_Short_All", "Change_in_Swap_Spread_All",
    "Change_in_M_Money_Long_All", "Change_in_M_Money_Short_All", "Change_in_M_Money_Spread_All",
    "Change_in_Other_Rept_Long_All", "Change_in_Other_Rept_Short_All", "Change_in_Other_Rept_Spread_All",
    "Change_in_Tot_Rept_Long_All", "Change_in_Tot_Rept_Short_All",
    "Change_in_NonRept_Long_All", "Change_in_NonRept_Short_All",
    "Pct_of_Open_Interest_All",
    "Pct_of_OI_Prod_Merc_Long_All", "Pct_of_OI_Prod_Merc_Short_All",
    "Pct_of_OI_Swap_Long_All", "Pct_of_OI_Swap_Short_All", "Pct_of_OI_Swap_Spread_All",
    "Pct_of_OI_M_Money_Long_All", "Pct_of_OI_M_Money_Short_All", "Pct_of_OI_M_Money_Spread_All",
    "Pct_of_OI_Other_Rept_Long_All", "Pct_of_OI_Other_Rept_Short_All", "Pct_of_OI_Other_Rept_Spread_All",
    "Pct_of_OI_Tot_Rept_Long_All", "Pct_of_OI_Tot_Rept_Short_All",
    "Pct_of_OI_NonRept_Long_All", "Pct_of_OI_NonRept_Short_All",
    "Traders_Tot_All",
    "Traders_Prod_Merc_Long_All", "Traders_Prod_Merc_Short_All",
    "Traders_Swap_Long_All", "Traders_Swap_Short_All", "Traders_Swap_Spread_All",
    "Traders_M_Money_Long_All", "Traders_M_Money_Short_All", "Traders_M_Money_Spread_All",
    "Traders_Other_Rept_Long_All", "Traders_Other_Rept_Short_All", "Traders_Other_Rept_Spread_All",
    "Conc_Gross_LE_4_TDR_Long_All", "Conc_Gross_LE_4_TDR_Short_All",
    "Conc_Gross_LE_8_TDR_Long_All", "Conc_Gross_LE_8_TDR_Short_All",
    "Conc_Net_LE_4_TDR_Long_All", "Conc_Net_LE_4_TDR_Short_All",
    "Conc_Net_LE_8_TDR_Long_All", "Conc_Net_LE_8_TDR_Short_All",
    "Contract_Units",
    "CFTC_Contract_Market_Code_Quotes", "CFTC_Market_Code_Quotes", "CFTC_Commodity_Code_Quotes",
    "CFTC_SubGroup_Code", "FutOnly_or_Combined",
]


def _download_current() -> pd.DataFrame | None:
    """Download and parse the current week's TFF text file (no header row)."""
    try:
        resp = requests.get(CURRENT_URL, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        # FinFutWk.txt has NO header row — assign column names explicitly
        df = pd.read_csv(
            io.BytesIO(resp.content),
            header=None,
            names=_CURRENT_WEEK_COLS,
            dtype=str,
            low_memory=False,
        )
        df.columns = [c.strip() for c in df.columns]
        return df
    except Exception as e:
        log.warning("COT current-week download failed: %s", e)
        return None


def _download_disagg_annual(year: int) -> pd.DataFrame | None:
    """Download and parse one year's Disaggregated Futures ZIP."""
    url = DISAGG_ANNUAL_URL.format(year=year)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.warning("COT disagg annual download failed for %d: %s", year, e)
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".csv"))]
        if not names:
            log.warning("COT disagg ZIP for %d contains no text files: %s", year, zf.namelist())
            return None
        data = zf.read(names[0])
        return _parse_csv_bytes(data)
    except Exception as e:
        log.warning("COT disagg ZIP parse failed for %d: %s", year, e)
        return None


def _download_disagg_current() -> pd.DataFrame | None:
    """Download and parse the current week's Disaggregated Futures plain-text file.

    c_disagg.txt may or may not have a header row — try both. If the file is
    headerless we assign _DISAGG_CURRENT_WEEK_COLS explicitly (same quirk as TFF FinFutWk.txt).
    """
    try:
        resp = requests.get(DISAGG_CURRENT_URL, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        log.warning("COT disagg current download failed: %s", e)
        return None

    # Try 1: file has a header row (normal CSV)
    try:
        return _parse_csv_bytes(resp.content)
    except ValueError:
        pass

    # Try 2: headerless — assign column names explicitly
    try:
        df = pd.read_csv(
            io.BytesIO(resp.content),
            header=None,
            names=_DISAGG_CURRENT_WEEK_COLS,
            dtype=str,
            low_memory=False,
        )
        df.columns = [c.strip() for c in df.columns]
        if _NAME_COL in df.columns:
            return df
        log.warning("COT disagg current headerless parse: still missing %s", _NAME_COL)
    except Exception as e:
        log.warning("COT disagg current headerless parse failed: %s", e)

    return None


def _match_instrument(name: str, instrument_list: list[dict]) -> str | None:
    """Return instrument code for a CFTC market name against the given list."""
    name_up = name.upper()
    for inst in instrument_list:
        for sub in inst["name_substr"]:
            if sub.upper() in name_up:
                return inst["code"]
    return None


def _resolve_date_col(df: pd.DataFrame) -> str | None:
    """Return whichever date column is present in this DataFrame."""
    for c in _DATE_COLS_ALT:
        if c in df.columns:
            return c
    return None


def _parse_date(raw: str, col_name: str) -> str:
    """Normalise a CFTC date value to YYYY-MM-DD regardless of source format."""
    raw = raw.strip()
    if col_name == "As_of_Date_In_Form_YYMMDD":
        # Format is YYMMDD e.g. "241231" → 2024-12-31
        try:
            return datetime.strptime(raw, "%y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            return raw
    return raw  # already YYYY-MM-DD


def _extract_rows(df: pd.DataFrame) -> list[dict]:
    """Filter a TFF dataframe down to tracked TFF instruments."""
    date_col = _resolve_date_col(df)
    if date_col is None:
        log.warning("COT TFF DataFrame has no recognised date column")
        return []

    want = [_NAME_COL, date_col, _OI_COL,
            _LEV_LONG, _LEV_SHORT,
            _ASSET_LONG, _ASSET_SHORT,
            _DEALER_LONG, _DEALER_SHORT]
    missing = [c for c in want if c not in df.columns]
    if missing:
        log.warning("COT TFF DataFrame missing columns: %s", missing)
        return []

    rows = []
    for _, row in df[want].iterrows():
        code = _match_instrument(str(row[_NAME_COL]), INSTRUMENTS)
        if code is None:
            continue
        try:
            rows.append({
                "code":          code,
                "report_date":   _parse_date(str(row[date_col]), date_col),
                "lev_long":      float(str(row[_LEV_LONG]).replace(",", "")),
                "lev_short":     float(str(row[_LEV_SHORT]).replace(",", "")),
                "asset_long":    float(str(row[_ASSET_LONG]).replace(",", "")),
                "asset_short":   float(str(row[_ASSET_SHORT]).replace(",", "")),
                "dealer_long":   float(str(row[_DEALER_LONG]).replace(",", "")),
                "dealer_short":  float(str(row[_DEALER_SHORT]).replace(",", "")),
                "open_interest": float(str(row[_OI_COL]).replace(",", "")),
            })
        except (ValueError, TypeError):
            continue
    return rows


def _extract_disagg_rows(df: pd.DataFrame) -> list[dict]:
    """Filter a Disaggregated dataframe down to tracked commodity instruments.

    Maps: M_Money → lev_long/lev_short, Prod_Merc → dealer_long/dealer_short,
          Swap → asset_long/asset_short (stored in same DB table as TFF data).
    Swap columns use a double underscore in CFTC files (Swap__Positions_*_All).
    """
    date_col = _resolve_date_col(df)
    if date_col is None:
        log.warning("COT Disagg DataFrame has no recognised date column")
        return []

    # Resolve swap column names — CFTC uses Swap__ (double underscore) in most files
    swap_long_col  = next((c for c in _SWAP_LONG_CANDIDATES  if c in df.columns), None)
    swap_short_col = next((c for c in _SWAP_SHORT_CANDIDATES if c in df.columns), None)

    required = [_NAME_COL, date_col, _OI_COL, _MM_LONG, _MM_SHORT, _PROD_LONG, _PROD_SHORT]
    missing = [c for c in required if c not in df.columns]
    if missing:
        log.warning("COT Disagg DataFrame missing required columns: %s", missing)
        return []

    if swap_long_col is None or swap_short_col is None:
        log.warning("COT Disagg: swap dealer columns not found — available: %s",
                    [c for c in df.columns if "swap" in c.lower()][:10])

    use_cols = required + ([swap_long_col, swap_short_col] if swap_long_col else [])

    rows = []
    for _, row in df[use_cols].iterrows():
        code = _match_instrument(str(row[_NAME_COL]), DISAGG_INSTRUMENTS)
        if code is None:
            continue
        try:
            swap_l = float(str(row[swap_long_col]).replace(",", ""))  if swap_long_col  else 0.0
            swap_s = float(str(row[swap_short_col]).replace(",", "")) if swap_short_col else 0.0
            rows.append({
                "code":          code,
                "report_date":   _parse_date(str(row[date_col]), date_col),
                "lev_long":      float(str(row[_MM_LONG]).replace(",", "")),
                "lev_short":     float(str(row[_MM_SHORT]).replace(",", "")),
                "asset_long":    swap_l,
                "asset_short":   swap_s,
                "dealer_long":   float(str(row[_PROD_LONG]).replace(",", "")),
                "dealer_short":  float(str(row[_PROD_SHORT]).replace(",", "")),
                "open_interest": float(str(row[_OI_COL]).replace(",", "")),
            })
        except (ValueError, TypeError):
            continue
    return rows


def _store(rows: list[dict]) -> int:
    if not rows:
        return 0
    added = 0
    with _lock:
        conn = _get_conn()
        for r in rows:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO cot_history
                    (code, report_date, lev_long, lev_short, lev_net,
                     asset_long, asset_short, asset_net,
                     dealer_long, dealer_short, open_interest)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        r["code"], r["report_date"],
                        r["lev_long"], r["lev_short"],
                        r["lev_long"] - r["lev_short"],
                        r["asset_long"], r["asset_short"],
                        r["asset_long"] - r["asset_short"],
                        r["dealer_long"], r["dealer_short"],
                        r["open_interest"],
                    ),
                )
                added += conn.execute("SELECT changes()").fetchone()[0]
            except Exception as e:
                log.debug("COT insert skip: %s", e)
        conn.commit()
    return added


# ── public API ────────────────────────────────────────────────────────────────

def refresh(years_back: int = 3) -> dict:
    """Download COT data (TFF + Disaggregated) for the last N years + current week."""
    current_year = datetime.now(timezone.utc).year
    errors: list[str] = []
    total_added = 0

    for year in range(current_year - years_back + 1, current_year + 1):
        # TFF (financial futures)
        df = _download_annual(year)
        if df is not None:
            rows = _extract_rows(df)
            n = _store(rows)
            total_added += n
            log.info("COT TFF %d: parsed %d rows, added %d new", year, len(rows), n)
        else:
            errors.append(f"TFF {year}: download failed")

        # Disaggregated (commodity futures)
        df = _download_disagg_annual(year)
        if df is not None:
            rows = _extract_disagg_rows(df)
            n = _store(rows)
            total_added += n
            log.info("COT disagg %d: parsed %d rows, added %d new", year, len(rows), n)
        else:
            errors.append(f"Disagg {year}: download failed")

    # Current week — TFF
    df = _download_current()
    if df is not None:
        rows = _extract_rows(df)
        n = _store(rows)
        total_added += n
        log.info("COT TFF current week: parsed %d rows, added %d new", len(rows), n)
    else:
        errors.append("TFF current week: download failed")

    # Current week — Disaggregated
    df = _download_disagg_current()
    if df is not None:
        rows = _extract_disagg_rows(df)
        n = _store(rows)
        total_added += n
        log.info("COT disagg current week: parsed %d rows, added %d new", len(rows), n)
    else:
        errors.append("Disagg current week: download failed")

    _meta_set("last_refresh", datetime.now(timezone.utc).isoformat())
    return {"added": total_added, "errors": errors}


def get_history(code: str, weeks: int = 156) -> list[dict]:
    """Historical lev-money net position for one instrument (newest first)."""
    return _rows(
        "SELECT report_date, lev_net, lev_long, lev_short, "
        "asset_net, dealer_long, dealer_short, open_interest "
        "FROM cot_history WHERE code=? "
        "ORDER BY report_date DESC LIMIT ?",
        (code.upper(), weeks),
    )


def _z_score(series: pd.Series, window: int = 52) -> float:
    if len(series) < 4:
        return 0.0
    n = min(len(series), window)
    window_data = series.iloc[:n]
    mean = window_data.mean()
    std = window_data.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float((series.iloc[0] - mean) / std)


def _crowding_label(z: float) -> str:
    if z >= 2.0:   return "crowded_long"
    if z >= 1.0:   return "leaning_long"
    if z <= -2.0:  return "crowded_short"
    if z <= -1.0:  return "leaning_short"
    return "neutral"


def get_snapshot() -> list[dict]:
    """Current crowding signals for all tracked instruments (TFF + Disaggregated)."""
    results = []
    for inst in ALL_INSTRUMENTS:
        code = inst["code"]
        rows = _rows(
            "SELECT report_date, lev_net, lev_long, lev_short, "
            "asset_net, dealer_long, dealer_short, open_interest "
            "FROM cot_history WHERE code=? ORDER BY report_date DESC LIMIT 104",
            (code,),
        )
        if not rows:
            results.append({
                "code": code, "label": inst["label"], "etf": inst["etf"],
                "report": inst.get("report", "tff"),
                "hasData": False, "reportDate": None, "levNet": None,
                "levLong": None, "levShort": None, "assetNet": None,
                "dealerNet": None, "openInterest": None,
                "zScore": None, "change1w": None, "change4w": None,
                "crowding": "no_data",
            })
            continue

        lev_nets = pd.Series([r["lev_net"] for r in rows])
        z = _z_score(lev_nets)
        change1w = float(lev_nets.iloc[0] - lev_nets.iloc[1]) if len(lev_nets) >= 2 else None
        change4w = float(lev_nets.iloc[0] - lev_nets.iloc[4]) if len(lev_nets) >= 5 else None

        r0 = rows[0]
        results.append({
            "code":         code,
            "label":        inst["label"],
            "etf":          inst["etf"],
            "report":       inst.get("report", "tff"),
            "hasData":      True,
            "reportDate":   r0["report_date"],
            "levNet":       r0["lev_net"],
            "levLong":      r0["lev_long"],
            "levShort":     r0["lev_short"],
            "assetNet":     r0["asset_net"],
            "dealerNet":    r0["dealer_long"] - r0["dealer_short"],
            "openInterest": r0["open_interest"],
            "zScore":       round(z, 2),
            "change1w":     change1w,
            "change4w":     change4w,
            "crowding":     _crowding_label(z),
        })
    return results


def last_refresh() -> str | None:
    return _meta_get("last_refresh")


def has_data() -> bool:
    rows = _rows("SELECT COUNT(*) as n FROM cot_history")
    return rows[0]["n"] > 0 if rows else False


def debug_download(year: int = 2024) -> dict:
    """Download the CFTC ZIP for one year and return diagnostic info without storing anything."""
    url = ANNUAL_URL.format(year=year)
    result: dict = {"url": url, "year": year}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["content_length"] = len(resp.content)
        resp.raise_for_status()
    except Exception as e:
        result["download_error"] = str(e)
        return result

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        result["zip_files"] = zf.namelist()
    except Exception as e:
        result["zip_error"] = str(e)
        result["raw_preview"] = resp.content[:500].decode("utf-8", errors="replace")
        return result

    file_results = []
    for name in zf.namelist():
        data = zf.read(name)
        file_info: dict = {"filename": name, "size": len(data)}

        # Try to sniff the delimiter and read columns
        for sep in (",", "|", "\t", ";"):
            try:
                df = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, low_memory=False, nrows=3)
                df.columns = [c.strip() for c in df.columns]
                file_info["delimiter"] = repr(sep)
                file_info["columns"] = list(df.columns)
                file_info["row_count_sample"] = len(df)
                file_info["has_name_col"] = _NAME_COL in df.columns
                file_info["has_date_col"] = _DATE_COL in df.columns
                if df.columns.tolist():
                    file_info["first_row"] = df.iloc[0].to_dict() if len(df) > 0 else {}
                # Read all unique market names to diagnose name-matching failures
                try:
                    df_names = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str,
                                           low_memory=False, usecols=[_NAME_COL])
                    unique = sorted(df_names[_NAME_COL].dropna().unique().tolist())
                    file_info["all_market_names"] = unique[:200]
                    file_info["total_unique_names"] = len(unique)
                except Exception:
                    pass
                break
            except Exception as e:
                file_info.setdefault("parse_errors", []).append(f"{sep!r}: {e}")

        file_results.append(file_info)

    result["files"] = file_results
    return result


def debug_disagg_download(year: int = 2024) -> dict:
    """Download the CFTC Disaggregated ZIP for one year and return diagnostic info."""
    url = DISAGG_ANNUAL_URL.format(year=year)
    result: dict = {"url": url, "year": year, "report": "disaggregated"}

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")
        result["content_length"] = len(resp.content)
        resp.raise_for_status()
    except Exception as e:
        result["download_error"] = str(e)
        return result

    try:
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        result["zip_files"] = zf.namelist()
    except Exception as e:
        result["zip_error"] = str(e)
        result["raw_preview"] = resp.content[:500].decode("utf-8", errors="replace")
        return result

    file_results = []
    for name in zf.namelist():
        data = zf.read(name)
        file_info: dict = {"filename": name, "size": len(data)}

        for sep in (",", "|", "\t", ";"):
            try:
                df = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, low_memory=False, nrows=3)
                df.columns = [c.strip() for c in df.columns]
                file_info["delimiter"] = repr(sep)
                file_info["columns"] = list(df.columns)
                file_info["has_name_col"]   = _NAME_COL in df.columns
                file_info["has_date_col"]   = _DATE_COL in df.columns
                file_info["has_mm_long"]    = _MM_LONG in df.columns
                file_info["has_mm_short"]   = _MM_SHORT in df.columns
                file_info["swap_long_col"]  = next(
                    (c for c in _SWAP_LONG_CANDIDATES  if c in df.columns), "NOT FOUND"
                )
                file_info["swap_short_col"] = next(
                    (c for c in _SWAP_SHORT_CANDIDATES if c in df.columns), "NOT FOUND"
                )
                try:
                    df_names = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str,
                                           low_memory=False, usecols=[_NAME_COL])
                    unique = sorted(df_names[_NAME_COL].dropna().unique().tolist())
                    file_info["all_market_names"] = unique[:300]
                    file_info["total_unique_names"] = len(unique)
                except Exception:
                    pass
                break
            except Exception as e:
                file_info.setdefault("parse_errors", []).append(f"{sep!r}: {e}")

        file_results.append(file_info)

    result["files"] = file_results
    return result

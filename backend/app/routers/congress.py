"""Congress STOCK Act trade disclosure endpoints."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import functools
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..analysis import indicators
from ..data import app_config, client, congress as cdata

router = APIRouter(prefix="/api/congress", tags=["congress"])

# Module-level cache so the scheduler can reuse a recently computed universe
# without re-fetching all price data.
_universe_cache: dict = {"result": None, "computed_at": 0.0, "days": 90, "min_buys": 1, "chamber": "both"}
_UNIVERSE_TTL = 4 * 3600


def _score(sig: dict) -> int:
    """Same multi-factor score used by the screener (−5 … +5)."""
    s = 0
    if sig.get("trend") == "uptrend":       s += 2
    elif sig.get("trend") == "downtrend":   s -= 2
    cross = sig.get("recentCross")
    if cross == "golden_cross":             s += 1
    elif cross == "death_cross":            s -= 1
    if sig.get("macdAboveSignal") is True:  s += 1
    elif sig.get("macdAboveSignal") is False: s -= 1
    r = sig.get("rsi")
    if r is not None:
        if r < 30:   s += 1
        elif r > 70: s -= 1
    return s


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            pass
    return None


def _price_on_or_before(candles: list[dict], target: datetime) -> float | None:
    """Closing price on the last trading day on or before target."""
    target_ts = target.timestamp()
    best: float | None = None
    best_ts: float = -1
    for c in candles:
        ts = float(c.get("time", 0))
        if ts <= target_ts and ts > best_ts:
            best_ts = ts
            best = float(c["close"])
    return best


def _annualize(ret: float, days: int) -> float | None:
    if days < 7:
        return None
    ann = ((1 + ret / 100) ** (365.0 / days) - 1) * 100
    return round(max(min(ann, 9999.9), -9999.9), 1)


def _enrich(ticker_data: dict, sells_index: dict | None = None) -> dict:
    """Add price-move columns and per-trade returns for politician scoring.

    sells_index: {(politician, ticker): [sell_txDate asc]} from the sells cache.
    When provided, closed positions use the actual sell price as the exit.
    Open positions (no matching sell) still use today's price.
    """
    ticker = ticker_data["ticker"]
    trades = ticker_data.get("trades", [])

    ref_trade = max(trades, key=lambda t: t.get("txDate") or "", default=None)
    if not ref_trade:
        return {**ticker_data, "enrichError": "no trades", "tradeReturns": []}

    tx_date   = _parse_date(ref_trade.get("txDate", ""))
    disc_date = _parse_date(ref_trade.get("disclosedDate", ""))

    try:
        hist    = client.get_history(ticker, period="2y")
        candles = hist.get("candles", [])
        if not candles:
            raise ValueError("no price data")

        price_now  = round(float(candles[-1]["close"]), 2)
        price_tx   = _price_on_or_before(candles, tx_date)   if tx_date   else None
        price_disc = _price_on_or_before(candles, disc_date) if disc_date else None

        sig = indicators.compute_signals(candles)
        signal_score = _score(sig)

        if price_tx   is not None: price_tx   = round(price_tx,   2)
        if price_disc is not None: price_disc = round(price_disc, 2)

        def pct(a, b):
            return round((b / a - 1) * 100, 1) if (a and b and a > 0) else None

        tx_to_disc_pct  = pct(price_tx,   price_disc)
        disc_to_now_pct = pct(price_disc, price_now)
        total_move_pct  = pct(price_tx,   price_now)

        d = disc_to_now_pct or 0
        if d >= 25:
            opportunity = "gone"
        elif d >= 12:
            opportunity = "fading"
        elif d <= -10:
            opportunity = "pullback"
        else:
            opportunity = "available"

        today = datetime.utcnow()
        trade_returns:  list[dict] = []
        enriched_trades: list[dict] = []

        for tr in trades:
            pol = tr.get("politician", "")
            td  = _parse_date(tr.get("txDate", ""))
            dd  = _parse_date(tr.get("disclosedDate", ""))

            p_buy  = _price_on_or_before(candles, td) if td else None
            p_disc_trade = _price_on_or_before(candles, dd) if dd else None

            tx_to_disc_trade = pct(p_buy, p_disc_trade)

            # Closed-position matching (FIFO)
            sell_date_str: str | None = None
            p_sell: float | None = None
            realized = False

            if td and p_buy and p_buy > 0 and sells_index:
                sell_dates = sells_index.get((pol, ticker), [])
                matched = next(
                    (sd for sd in sell_dates if _parse_date(sd) and _parse_date(sd) > td),
                    None,
                )
                if matched:
                    sell_dt = _parse_date(matched)
                    if sell_dt:
                        p_at_sell = _price_on_or_before(candles, sell_dt)
                        if p_at_sell and p_at_sell > 0:
                            sell_date_str = matched
                            p_sell = round(p_at_sell, 2)
                            realized = True

            if p_buy and p_buy > 0:
                if realized and p_sell is not None:
                    exit_dt   = _parse_date(sell_date_str)
                    days_held = max((exit_dt - td).days, 1) if (exit_dt and td) else 1
                    ret       = round((p_sell / p_buy - 1) * 100, 1)
                    exit_price = p_sell
                else:
                    days_held  = max((today - td).days, 1) if td else 1
                    ret        = round((price_now / p_buy - 1) * 100, 1)
                    exit_price = price_now

                trade_returns.append({
                    "politician":          pol,
                    "ticker":              ticker,
                    "txDate":              tr.get("txDate", ""),
                    "sellDate":            sell_date_str,
                    "realized":            realized,
                    "priceAtBuy":          round(p_buy, 2),
                    "priceAtExit":         exit_price,
                    "returnPct":           ret,
                    "annualizedReturnPct": _annualize(ret, days_held),
                    "daysHeld":            days_held,
                    "win":                 ret > 0,
                })
            else:
                ret       = None
                exit_price = None

            # Embed price & return data directly into the trade record for the frontend
            enriched_trades.append({
                **tr,
                "priceAtBuy":          round(p_buy, 2) if p_buy else None,
                "priceAtDisclosure":   round(p_disc_trade, 2) if p_disc_trade else None,
                "txToDisclosurePct":   tx_to_disc_trade,
                "totalReturnPct":      ret,
                "priceAtExit":         exit_price,
                "realized":            realized,
                "sellDate":            sell_date_str,
            })

        return {
            **ticker_data,
            "trades":             enriched_trades,   # override with enriched version
            "priceAtTx":          price_tx,
            "priceAtDisclosure":  price_disc,
            "priceNow":           price_now,
            "txToDisclosurePct":  tx_to_disc_pct,
            "disclosureToNowPct": disc_to_now_pct,
            "totalMovePct":       total_move_pct,
            "opportunity":        opportunity,
            "refTxDate":          ref_trade.get("txDate", ""),
            "refDisclosedDate":   ref_trade.get("disclosedDate", ""),
            "tradeReturns":       trade_returns,
            "score":              signal_score,
        }
    except Exception as e:
        return {
            **ticker_data,
            "priceAtTx": None, "priceAtDisclosure": None, "priceNow": None,
            "txToDisclosurePct": None, "disclosureToNowPct": None,
            "totalMovePct": None, "opportunity": "unknown", "score": None,
            "enrichError": str(e),
            "tradeReturns": [],
        }


def _build_universe(days: int, min_buys: int, chamber: str) -> dict:
    """Core congress universe logic — shared by the HTTP endpoint and the scheduler."""
    raw_tickers = cdata.get_top_tickers(days_back=days, min_buys=min_buys, chamber=chamber)

    # Pre-fetch sell index once — used by all _enrich calls to match closed positions
    sells_index = cdata.get_sells_by_pol_ticker(days_back=max(days * 2, 365))

    # Enrich in parallel — one price-history fetch per ticker
    enriched: list[dict] = []
    _enrich_with_sells = functools.partial(_enrich, sells_index=sells_index)
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_enrich_with_sells, t): t for t in raw_tickers}
        for fut in as_completed(futures):
            enriched.append(fut.result())

    # Fetch sell transactions for the politician modal (full list, not just universe tickers)
    recent_sells = cdata.get_recent_sells(days_back=days)

    # ── Politician performance stats ──────────────────────────────────────────
    pol_buckets: dict[str, dict] = {}
    for t in enriched:
        for tr in t.get("tradeReturns", []):
            pol = tr.get("politician", "")
            if not pol:
                continue
            if pol not in pol_buckets:
                pol_buckets[pol] = {
                    "wins": 0, "trades": 0, "returns": [], "annualized": [],
                    "realizedWins": 0, "realizedLosses": 0, "realizedReturns": [],
                    "openReturns": [],
                }
            b = pol_buckets[pol]
            b["trades"] += 1
            b["returns"].append(tr["returnPct"])
            if tr.get("annualizedReturnPct") is not None:
                b["annualized"].append(tr["annualizedReturnPct"])
            if tr["win"]:
                b["wins"] += 1
            if tr.get("realized"):
                b["realizedReturns"].append(tr["returnPct"])
                if tr["win"]:
                    b["realizedWins"] += 1
                else:
                    b["realizedLosses"] += 1
            else:
                b["openReturns"].append(tr["returnPct"])

    politician_stats: dict[str, dict] = {}
    for pol, b in pol_buckets.items():
        n    = b["trades"]
        rets = b["returns"]
        ann  = b["annualized"]
        rr   = b["realizedReturns"]
        orr  = b["openReturns"]
        rc   = len(rr)
        oc   = len(orr)

        win_rate          = round(b["wins"] / n * 100, 1) if n else 0.0
        avg_gain          = round(sum(rets) / len(rets), 1) if rets else 0.0
        avg_ann           = round(sum(ann) / len(ann), 1) if ann else None
        realized_win_rate = round(b["realizedWins"] / rc * 100, 1) if rc else None
        avg_realized_gain = round(sum(rr) / rc, 1) if rc else None
        avg_open_gain     = round(sum(orr) / oc, 1) if oc else None

        politician_stats[pol] = {
            "trades":            n,
            "wins":              b["wins"],
            "winRate":           win_rate,
            "avgGain":           avg_gain,
            "avgAnnualizedGain": avg_ann,
            "realizedCount":     rc,
            "openCount":         oc,
            "realizedWins":      b["realizedWins"],
            "realizedLosses":    b["realizedLosses"],
            "realizedWinRate":   realized_win_rate,
            "avgRealizedGain":   avg_realized_gain,
            "avgOpenGain":       avg_open_gain,
        }

    # ── Investor-quality badge + contrarian flag per ticker ───────────────────
    for t in enriched:
        pols = t.get("politicians", [])
        effective_rates = []
        effective_ann = []
        for p in pols:
            if p not in politician_stats:
                continue
            ps = politician_stats[p]
            wr = ps["realizedWinRate"] if (ps["realizedWinRate"] is not None and ps["realizedCount"] >= 2) \
                 else ps["winRate"]
            effective_rates.append(wr)
            if ps.get("avgAnnualizedGain") is not None:
                effective_ann.append(ps["avgAnnualizedGain"])

        avg_wr  = sum(effective_rates) / len(effective_rates) if effective_rates else None
        avg_ann = round(sum(effective_ann) / len(effective_ann), 1) if effective_ann else None
        max_ann = round(max(effective_ann), 1) if effective_ann else None
        if avg_wr is None:
            quality = "unknown"
        elif avg_wr >= 65:
            quality = "sharp"
        elif avg_wr >= 45:
            quality = "mixed"
        else:
            quality = "weak"
        t["investorQuality"]      = quality
        t["investorQualityScore"] = round(avg_wr, 1) if avg_wr is not None else None
        t["avgAnnualizedGain"]    = avg_ann
        t["maxAnnualizedGain"]    = max_ann  # best individual politician — used for quality gating
        score = t.get("score")
        t["contrarian"] = (quality == "sharp" and score is not None and score <= -1)
        t.pop("tradeReturns", None)

    order = {"available": 0, "pullback": 1, "fading": 2, "gone": 3, "unknown": 4}
    enriched.sort(key=lambda t: (order.get(t.get("opportunity", "unknown"), 4),
                                  -t["uniquePoliticians"], -t["buyCount"]))

    result = {
        "tickers":         enriched,
        "universe":        [t["ticker"] for t in enriched],
        "daysBack":        days,
        "minBuys":         min_buys,
        "chamber":         chamber,
        "cacheInfo":       cdata.cache_info(),
        "politicianStats": politician_stats,
        "recentSells":     recent_sells,
    }

    # Store in module cache so the scheduler can reuse it without re-fetching
    _universe_cache.update({"result": result, "computed_at": time.time(),
                             "days": days, "min_buys": min_buys, "chamber": chamber})
    return result


def get_cached_smart_buys() -> list[dict]:
    """Return contrarian tickers from the last universe build (or trigger a fresh build).

    Called by the scheduler. Returns tickers with contrarian=True — meaning
    their technical score is weak (≤ -1) but high-quality politicians are buying.
    """
    age = time.time() - _universe_cache["computed_at"]
    if _universe_cache["result"] is None or age > _UNIVERSE_TTL:
        _build_universe(90, 1, "both")
    result = _universe_cache.get("result") or {}
    return [t for t in result.get("tickers", []) if t.get("contrarian")]


@router.get("/ticker/{symbol}")
def congress_ticker_detail(symbol: str):
    """Return cached congress data for a specific ticker.

    Uses the module-level universe cache; triggers a fresh build if the cache
    is empty or stale. Returns politician stats and trade list for the ticker.
    """
    symbol = symbol.upper()
    age = time.time() - _universe_cache["computed_at"]
    if _universe_cache["result"] is None or age > _UNIVERSE_TTL:
        _build_universe(90, 1, "both")
    result   = _universe_cache.get("result") or {}
    tickers  = result.get("tickers", [])
    pol_stats = result.get("politicianStats", {})

    ticker_data = next((t for t in tickers if t.get("ticker", "").upper() == symbol), None)
    relevant_pols = ticker_data.get("politicians", []) if ticker_data else []

    return {
        "symbol":          symbol,
        "found":           ticker_data is not None,
        "ticker":          ticker_data,
        "politicianStats": {p: pol_stats[p] for p in relevant_pols if p in pol_stats},
        "cacheAgeSec":     round(time.time() - _universe_cache.get("computed_at", 0)),
    }


@router.get("/universe")
def congress_universe(days: int = 90, minBuys: int = 1, chamber: str = "both"):
    """Tickers ranked by politician purchase activity with price-move enrichment.

    days: look-back window (7–365)
    minBuys: minimum total purchases to include a ticker
    chamber: both | house | senate
    """
    if chamber not in ("both", "house", "senate"):
        raise HTTPException(400, "chamber must be both, house, or senate")
    days    = min(max(days, 7), 365)
    minBuys = min(max(minBuys, 1), 50)
    return _build_universe(days, minBuys, chamber)


@router.get("/buys")
def recent_buys(days: int = 90, chamber: str = "both"):
    """Raw list of recent purchase transactions, newest first."""
    if chamber not in ("both", "house", "senate"):
        raise HTTPException(400, "chamber must be both, house, or senate")
    days = min(max(days, 7), 365)
    trades = cdata.get_recent_buys(days_back=days, chamber=chamber)
    return {"trades": sorted(trades, key=lambda t: t["txDate"], reverse=True),
            "count": len(trades), "daysBack": days}


@router.post("/refresh")
def force_refresh():
    """Force re-fetch of raw disclosure data (ignores 6h cache)."""
    info = cdata.refresh()
    return {"ok": True, "cacheInfo": info}


class ApiKeyRequest(BaseModel):
    quiver_api_key: str


class CsvImportRequest(BaseModel):
    csv_text: str


@router.get("/config")
def get_config():
    """Return whether a Quiver API key or CSV import is configured."""
    return {
        "hasKey":    bool(app_config.get_quiver_key()),
        "hasImport": cdata.has_import(),
    }


@router.post("/config")
def set_config(req: ApiKeyRequest):
    """Save the Quiver Quantitative API key and immediately test it."""
    app_config.set_quiver_key(req.quiver_api_key)
    # Force next request to re-fetch with the new key
    cdata.reset_cache()
    info = cdata.refresh()
    if info["errors"]:
        return {"ok": False, "error": info["errors"][0], "cacheInfo": info}
    return {"ok": True, "cacheInfo": info}


@router.delete("/config")
def delete_config():
    """Remove the Quiver API key."""
    app_config.set_quiver_key("")
    cdata.reset_cache()
    return {"ok": True}


@router.post("/import")
def import_csv(req: CsvImportRequest):
    """Import congressional trades from a CSV paste."""
    result = cdata.import_csv(req.csv_text)
    return {**result, "cacheInfo": cdata.cache_info()}


@router.delete("/import")
def delete_import():
    """Clear previously imported CSV data."""
    cdata.clear_imported()
    return {"ok": True}


@router.get("/debug")
def debug():
    """Diagnose data fetch — shows cache state and raw sample rows."""
    return {
        "cacheInfo": cdata.cache_info(),
        "sample": cdata.get_debug_sample(3),
    }

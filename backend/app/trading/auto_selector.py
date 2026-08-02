"""Automatic candidate selection and position sizing for the paper engine.

Scheduled at 10:05 AM and 4:05 PM ET (after the regular paper-engine run).
Self-contained: scans the watchlist and refreshes smart-buy alerts before
gathering candidates, so a fresh run always sees this cycle's data regardless
of whether the separate scan/smart-buy jobs already ran. Gathers candidates
from three signal sources, ranks them, sizes positions using ATR-based risk,
creates paper instances, and fires an engine run.

Signal sources
──────────────
  1. Technical (score ≥ 3)   — strong trend + MACD + RSI alignment
  2. Sustained (streak ≥ 3)  — score ≥ 2 held across 3+ consecutive scans
  3. Smart Buy               — score ≤ -1 but sharp politicians (≥ 65% win-rate) buying

Strategy assignment
───────────────────
  Technical / Sustained → ema_cross_9_21  (trend-following)
  Oversold technical    → rsi_revert       (mean reversion when RSI < 35)
  Smart Buy             → rsi_revert       (buying the dip; price-recovery thesis)

Position sizing
───────────────
  dollar_risk    = equity × risk_pct   (1% normal, 0.5% smart-buy)
  shares         = dollar_risk / (2 × ATR)
  allocation_usd = shares × price  (capped at 10% of equity)

Portfolios
──────────
  Each strategy bucket (Smart Buy, Technical/Sustained, Crypto) has its own
  real capital pool and equity curve (see store.py's portfolios table) — no
  cross-bucket interleaving/balancing is needed since one bucket can never
  compete with another for the same dollars. Every candidate is tagged with
  its target portfolioId at gather time (_gather_candidates), and selection/
  sizing/cash-headroom checks run independently per portfolio. A symbol can
  be held independently by more than one portfolio at once — see
  routers/paper.py's overlap endpoint for cross-portfolio visibility.

  Crypto doesn't get its own signal source — Sources 1/2 (technical score,
  sustained streak) already work off whatever's in the scan watchlist, so a
  crypto symbol scanned there produces a candidate exactly like an equity
  one. The only difference is which portfolio it's routed to, decided by
  CRYPTO_SYMBOLS / _portfolio_for() at gather time. Source 3 (smart buy)
  never applies to crypto — congressional trade disclosures don't cover it.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import groupby

from ..analysis import indicators as ind
from ..data import client
from . import store
from .engine import run_engine

log = logging.getLogger("paper.auto_selector")

# ── tunables ──────────────────────────────────────────────────────────────────
RISK_PCT_NORMAL    = 0.01   # 1 % of equity risked per standard trade
RISK_PCT_SMART_BUY = 0.005  # 0.5 % for contrarian/uncertain setups
MAX_POSITION_PCT   = 0.10   # never put more than 10 % equity in one position
MIN_CASH_PCT       = 0.10   # skip new positions if cash < 10 % of equity
TECH_MIN_SCORE     = 3      # minimum technical score to qualify
SUSTAINED_MIN_STREAK = 3    # consecutive scan streak required
SUSTAINED_MIN_SCORE  = 2    # score threshold for streak counting
SMART_BUY_MAX_DAYS        = 30    # only use smart-buy alerts from the last N days
CONGRESS_MIN_ANN_RETURN   = 60.0  # min best-politician annualized return to tag a Technical/
                                   # Sustained pick with "congress" (informational tag only)
CONGRESS_TAG_MIN_QUALITY  = {"sharp", "mixed"}  # exclude "weak"/"unknown" quality from the tag
SMART_BUY_RSI_ENTRY      = 45    # rsi_revert buyBelow for Smart Buy instances (default is 30) —
                                  # the contrarian thesis is congressional conviction, not a second
                                  # oversold trigger stacked on top, so entry is loosened to convert
                                  # more of the bucket's instances into actual filled positions
SMART_BUY_OPTIONS_BONUS  = 1.0   # composite-score bonus when the qualifying politician bought via
                                  # options rather than shares — a leveraged, time-bound bet is a
                                  # stronger conviction signal than a plain stock purchase

# Validated against client.get_history() at build time — every symbol below returned
# 180+ daily bars over a 6mo window. A few originally-proposed tickers (MATIC-USD,
# UNI-USD, APT-USD) had no accessible Yahoo/Polygon data and were dropped.
CRYPTO_SYMBOLS = {
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "BNB-USD", "ADA-USD", "DOGE-USD",
    "AVAX-USD", "DOT-USD", "LINK-USD", "LTC-USD", "BCH-USD", "ATOM-USD", "XLM-USD",
    "ETC-USD", "FIL-USD", "ARB-USD", "OP-USD", "NEAR-USD", "ICP-USD", "HBAR-USD",
    "ALGO-USD", "AAVE-USD", "MKR-USD", "SAND-USD", "TRX-USD", "INJ-USD",
}


def _portfolio_for(symbol: str) -> str:
    """Which portfolio a technical/sustained candidate belongs to. Smart Buy is never
    the default here — it's only ever assigned by Source 3 overriding this."""
    return store.BUCKET_CRYPTO if symbol in CRYPTO_SYMBOLS else store.BUCKET_TECHNICAL_SUSTAINED

# ── module-level result cache (for the status endpoint) ───────────────────────
_last_result: dict = {}


def get_last_result() -> dict:
    return _last_result


# ── helpers ───────────────────────────────────────────────────────────────────

def _open_plan_symbols(portfolio_id: str) -> set[str]:
    """Symbols with a currently open trade plan within this portfolio. Scoped so a
    symbol another portfolio already holds doesn't block this one — portfolios can
    independently hold the same symbol now."""
    return {p["symbol"].upper() for p in store.open_plans() if p.get("portfolio_id") == portfolio_id}


def _enabled_instance_symbols(portfolio_id: str) -> set[str]:
    """Symbols that already have an enabled instance within this portfolio."""
    return {i["symbol"].upper() for i in store.list_instances()
            if i["enabled"] and i.get("portfolio_id") == portfolio_id}


def _get_equity_cash(portfolio_id: str) -> tuple[float, float]:
    from .broker import get_active_broker
    mtm = get_active_broker(portfolio_id).mark_to_market()
    return mtm["equity"], mtm["cash"]


def _pick_strategy(signal_type: str, rsi: float | None) -> str:
    if signal_type == "smart_buy":
        return "rsi_revert"  # contrarian — buying the dip
    if rsi is not None and rsi < 35:
        return "rsi_revert"  # oversold + bullish alignment
    return "ema_cross_9_21"  # standard trend-following


def _existing_bucket_allocation() -> dict[str, float]:
    """Dollar allocation currently committed to enabled instances, split by portfolio
    — informational, surfaced in the auto-select journal."""
    totals = {p["id"]: 0.0 for p in store.list_portfolios()}
    for inst in store.list_instances():
        if not inst["enabled"]:
            continue
        pid = inst.get("portfolio_id") or store.bucket_of_tags(inst.get("source_tags"))
        totals[pid] = totals.get(pid, 0.0) + inst["allocation_usd"]
    return totals


def _size_position(equity: float, price: float, atr: float, smart_buy: bool) -> float:
    risk_pct = RISK_PCT_SMART_BUY if smart_buy else RISK_PCT_NORMAL
    dollar_risk = equity * risk_pct
    if atr <= 0 or price <= 0:
        return 0.0
    shares = dollar_risk / (2.0 * atr)
    return round(min(shares * price, equity * MAX_POSITION_PCT), 2)


def _candidate_score(c: dict) -> float:
    """Composite rank — higher = higher priority for allocation.

    Smart Buy candidates are already gated to "sharp" politicians (≥65% win rate)
    before they ever reach this function (see congress.py's contrarian flag), so
    there's no additional threshold to clear here — instead every smart-buy
    candidate gets a continuous score from its quality/return/conviction stats,
    so they spread out instead of tying at a flat baseline.
    """
    score = float(max(0, c.get("techScore", 0)))
    score += min(c.get("streak", 0) * 0.5, 3.0)
    if c.get("smartBuy"):
        iq  = c.get("investorQualityScore") or 0        # win rate 0-100 (already ≥65 to qualify)
        ann = c.get("maxAnnualizedGain")
        if ann is None:                                  # fall back if max wasn't recorded
            ann = c.get("avgAnnualizedGain")
        ann = max(ann or 0, 0)
        buy_count = c.get("buyCount") or 0
        score += max(iq - 50, 0) / 25       # win-rate edge above 50%: 65%→+0.6, 100%→+2.0
        score += min(ann / 50, 3.0)         # annualized return: 50%→+1, 150%+→capped at +3
        score += min(buy_count * 0.2, 1.0)  # conviction: more buys backing it → up to +1
        if c.get("hasOptionsActivity"):
            score += SMART_BUY_OPTIONS_BONUS
    return score


def _selection_thesis(c: dict) -> str:
    """Human-readable explanation of why the auto-selector picked this ticker —
    distinct from the strategy-entry thesis engine.py writes at fill time, which
    only explains the technical trigger, not the original selection rationale."""
    sources = c.get("sources", [])
    parts: list[str] = []

    if "technical" in sources or "sustained" in sources:
        bits = []
        if "technical" in sources:
            bits.append(f"technical score {c.get('techScore')} (≥{TECH_MIN_SCORE} threshold)")
        if "sustained" in sources:
            bits.append(
                f"held for {c.get('streak', 0)} consecutive scan(s) at score "
                f"≥{SUSTAINED_MIN_SCORE}"
            )
        extra = []
        if c.get("rsi") is not None:
            extra.append(f"RSI {c['rsi']:.1f}")
        if c.get("atrPct") is not None:
            extra.append(f"ATR {c['atrPct']:.1f}% of price")
        extra_str = f" ({', '.join(extra)})" if extra else ""
        parts.append(f"Technical signal: {' and '.join(bits)}{extra_str}.")

    if c.get("smartBuy"):
        iq  = c.get("investorQualityScore")
        ann = c.get("maxAnnualizedGain")
        if ann is None:
            ann = c.get("avgAnnualizedGain")
        buys = c.get("buyCount")
        if iq is not None and ann is not None:
            parts.append(
                f"Smart Buy (contrarian): technical score {c.get('techScore')} at detection was"
                f" bearish/neutral, but {buys or 'multiple'} politician purchase(s) from a 'sharp'"
                f" investor track record ({iq:.1f}% win rate, {ann:.1f}% best annualized return)"
                " suggested informed buying against a weak chart."
            )
        else:
            parts.append(
                "Smart Buy (contrarian): sharp politician buying against a bearish/neutral"
                " technical setup."
            )
        if c.get("hasOptionsActivity"):
            from ..routers.congress import MIN_OPTION_RUNWAY_DAYS  # local import — avoids circular import at module load
            parts.append(
                "At least one of these purchases was a bullish call option (not a put, and"
                f" with {MIN_OPTION_RUNWAY_DAYS}+ days of runway left between disclosure and"
                " expiration) rather than shares — a leveraged, time-bound bet, and a stronger"
                f" conviction signal than a plain stock buy (+{SMART_BUY_OPTIONS_BONUS} to the"
                " ranking score)."
            )
    elif "congress" in sources:
        parts.append(
            "Also backed by Politician Trades: best individual politician annualized return"
            f" ≥{CONGRESS_MIN_ANN_RETURN:.0f}% with at least 'mixed' aggregate investor quality"
            " across everyone who bought this ticker."
        )

    if "smart_universe" in sources:
        parts.append("Ticker also sits in a top-ranked sector per the Smart Universe scan.")

    parts.append(f"Composite ranking score: {round(_candidate_score(c), 2)}.")
    return " ".join(parts)


def _selection_snapshot(c: dict) -> dict:
    """Raw signal values behind the selection thesis, for detail display/debugging."""
    return {
        "signalType":           c.get("signalType"),
        "sources":              c.get("sources", []),
        "techScore":            c.get("techScore"),
        "streak":               c.get("streak"),
        "rsi":                  c.get("rsi"),
        "atrPct":               c.get("atrPct"),
        "investorQualityScore": c.get("investorQualityScore"),
        "avgAnnualizedGain":    c.get("avgAnnualizedGain"),
        "maxAnnualizedGain":    c.get("maxAnnualizedGain"),
        "buyCount":             c.get("buyCount"),
        "hasOptionsActivity":   c.get("hasOptionsActivity", False),
        "compositeScore":       round(_candidate_score(c), 2),
    }


# ── candidate gathering ───────────────────────────────────────────────────────

def _smart_universe_tickers() -> set[str]:
    """Tickers from the top-ranked sectors in the Smart Universe cache (no network call)."""
    try:
        from ..routers.universe import _cache as uni_cache
        sectors = uni_cache.get("sectors") or []
        return {
            ticker.upper()
            for s in sectors
            if s.get("rank", 999) <= 2          # top-2 sectors by default
            for ticker in s.get("holdings", [])
        }
    except Exception:
        return set()


def _congress_tickers() -> dict[str, dict]:
    """Return {ticker: {maxAnnualizedGain, avgAnnualizedGain, investorQuality}} from the
    enriched congress universe cache.

    Uses the same cache populated by _ensure_smart_buys_fresh / the scheduler, so no
    extra network call is needed when the cache is warm.
    """
    try:
        from ..routers.congress import _universe_cache
        result = _universe_cache.get("result") or {}
        return {
            t["ticker"].upper(): {
                "maxAnnualizedGain": t.get("maxAnnualizedGain"),
                "avgAnnualizedGain": t.get("avgAnnualizedGain"),
                "investorQuality":   t.get("investorQuality"),
            }
            for t in result.get("tickers", [])
        }
    except Exception:
        return {}


def _gather_candidates() -> list[dict]:
    """Pull and merge candidates from all five signal sources."""
    from ..routers.scan import _db_rows

    candidates: dict[str, dict] = {}

    # ── Source 1: high technical score ────────────────────────────────────────
    cutoff = time.time() - 4 * 3600
    for r in _db_rows(
        "SELECT symbol, score, trend, rsi, atr_pct, last_price "
        "FROM scan_history WHERE scanned_at >= ? AND score >= ? "
        "ORDER BY score DESC LIMIT 30",
        (cutoff, TECH_MIN_SCORE),
    ):
        sym = r["symbol"].upper()
        candidates[sym] = {
            "symbol":      sym,
            "techScore":   r["score"],
            "streak":      0,
            "smartBuy":    False,
            "portfolioId": _portfolio_for(sym),
            "rsi":         r["rsi"],
            "atrPct":      r["atr_pct"],
            "lastPrice":   r["last_price"],
            "signalType":  "technical",
            "sources":     ["technical"],
        }

    # ── Source 2: sustained streak ────────────────────────────────────────────
    cutoff2 = time.time() - 90 * 86400
    all_hist = _db_rows(
        "SELECT symbol, score, scanned_at FROM scan_history "
        "WHERE scanned_at > ? ORDER BY symbol, scanned_at DESC",
        (cutoff2,),
    )
    for sym, grp in groupby(all_hist, key=lambda r: r["symbol"]):
        scans = list(grp)
        streak = 0
        for s in scans:
            if s["score"] >= SUSTAINED_MIN_SCORE:
                streak += 1
            else:
                break
        if streak < SUSTAINED_MIN_STREAK:
            continue
        sym = sym.upper()
        latest = scans[0]
        if sym in candidates:
            candidates[sym]["streak"] = streak
            candidates[sym]["sources"].append("sustained")
        else:
            candidates[sym] = {
                "symbol":     sym,
                "techScore":  latest["score"],
                "streak":     streak,
                "smartBuy":   False,
                "portfolioId": _portfolio_for(sym),
                "rsi":        None,
                "atrPct":     None,
                "lastPrice":  None,
                "signalType": "sustained",
                "sources":    ["sustained"],
            }

    # ── Source 3: smart buy alerts ────────────────────────────────────────────
    seen_sba: set[str] = set()
    for r in _db_rows(
        "SELECT ticker, score_at_detection, investor_quality_score, avg_annualized_gain, "
        "max_ann_gain, buy_count, has_options_activity "
        "FROM smart_buy_alerts WHERE detected_at >= ? "
        "ORDER BY detected_at DESC",
        (time.time() - SMART_BUY_MAX_DAYS * 86400,),
    ):
        sym = r["ticker"].upper()
        if sym in seen_sba:
            continue
        seen_sba.add(sym)
        has_options = bool(r["has_options_activity"])
        if sym in candidates:
            candidates[sym]["smartBuy"] = True
            candidates[sym]["portfolioId"] = store.BUCKET_SMART_BUY  # overrides technical/sustained
            candidates[sym]["investorQualityScore"] = r["investor_quality_score"]
            candidates[sym]["avgAnnualizedGain"]    = r["avg_annualized_gain"]
            candidates[sym]["maxAnnualizedGain"]    = r["max_ann_gain"]
            candidates[sym]["buyCount"]             = r["buy_count"]
            candidates[sym]["hasOptionsActivity"]   = has_options
            candidates[sym]["sources"].append("smart_buy")
        else:
            candidates[sym] = {
                "symbol":               sym,
                "techScore":            r["score_at_detection"],
                "streak":               0,
                "smartBuy":             True,
                "portfolioId":          store.BUCKET_SMART_BUY,
                "rsi":                  None,
                "atrPct":               None,
                "lastPrice":            None,
                "signalType":           "smart_buy",
                "investorQualityScore": r["investor_quality_score"],
                "avgAnnualizedGain":    r["avg_annualized_gain"],
                "maxAnnualizedGain":    r["max_ann_gain"],
                "buyCount":             r["buy_count"],
                "hasOptionsActivity":   has_options,
                "sources":              ["smart_buy"],
            }

    # ── Cross-reference: Smart Universe + Politician Trades ───────────────────
    uni_syms  = _smart_universe_tickers()  # sectors cache — no network call
    cong_qual = _congress_tickers()        # {ticker: {maxAnnualizedGain, avgAnnualizedGain, investorQuality}}

    for c in candidates.values():
        if c["symbol"] in uni_syms and "smart_universe" not in c["sources"]:
            c["sources"].append("smart_universe")
        if not c.get("smartBuy") and "congress" not in c["sources"]:
            info = cong_qual.get(c["symbol"])
            if info:
                ann = info.get("maxAnnualizedGain")
                if ann is None:                              # fall back if max wasn't recorded
                    ann = info.get("avgAnnualizedGain")
                quality = info.get("investorQuality")
                if (ann is not None and ann >= CONGRESS_MIN_ANN_RETURN
                        and quality in CONGRESS_TAG_MIN_QUALITY):
                    c["sources"].append("congress")

    return list(candidates.values())


def _fetch_live(symbol: str) -> dict:
    """Return {price, atr} or {} on failure."""
    try:
        hist = client.get_history(symbol, period="2y")
        candles = hist.get("candles", [])
        if len(candles) < 20:
            return {}
        df  = ind.to_df(candles)
        atr = float(ind.atr(df).dropna().iloc[-1])
        return {"price": round(float(candles[-1]["close"]), 4), "atr": round(atr, 4)}
    except Exception:
        return {}


# ── main entry point ─────────────────────────────────────────────────────────

def _run_watchlist_scan() -> None:
    """Scan the watchlist so Technical/Sustained candidates reflect this cycle's data."""
    try:
        from ..routers.scan import run_scheduled_scan
        result = run_scheduled_scan()
        log.info("auto-selector: watchlist scan done — %d scanned, %d errors",
                 result["scanned"], len(result["errors"]))
    except Exception as e:
        log.warning("auto-selector: watchlist scan failed: %s", e)


def _ensure_smart_buys_fresh() -> None:
    """Build the congress universe and record smart-buy alerts if the cache is stale."""
    try:
        from ..routers.congress import _universe_cache, _UNIVERSE_TTL, get_cached_smart_buys
        from ..routers.scan import record_smart_buys
        age = time.time() - (_universe_cache.get("computed_at") or 0)
        if age > _UNIVERSE_TTL:
            log.info("auto-selector: congress cache stale (%.0f min old) — refreshing smart buys", age / 60)
            alerts = get_cached_smart_buys()  # triggers _build_universe internally if stale
            inserted = record_smart_buys(alerts)
            log.info("auto-selector: smart-buy refresh done — %d contrarian tickers, %d new alerts", len(alerts), inserted)
        else:
            log.debug("auto-selector: congress cache fresh (%.0f min old), skipping refresh", age / 60)
    except Exception as e:
        log.warning("auto-selector: smart-buy refresh failed: %s", e)


def _record_journal(run_kind: str, result: dict) -> None:
    """Persist a journal entry for this invocation, regardless of outcome."""
    try:
        store.record_auto_select_run(
            run_kind=run_kind,
            candidates=result.get("candidates", 0),
            eligible=result.get("eligible", 0),
            selected=result.get("selected", 0),
            skipped=result.get("skipped"),
            equity=result.get("equity"),
            cash=result.get("cash"),
            selections=result.get("selections", []),
            errors=result.get("errors", []),
            bucket_totals=_existing_bucket_allocation(),
        )
    except Exception:
        log.exception("auto-selector: failed to record journal entry")


def run_auto_selection(run_kind: str = "auto", portfolio_ids: list[str] | None = None) -> dict:
    """Select candidates, create instances, fire engine. Called by scheduler.

    Every invocation writes a journal entry (store.auto_select_runs, surfaced in the
    Trade Journal) recording what happened and why — including skipped or empty
    cycles, not just successful picks. portfolio_ids optionally scopes the whole
    cycle to specific portfolios (used by the 24/7 crypto cadence so it doesn't
    redundantly re-run equity selection on every tick) — None runs every portfolio.
    """
    global _last_result
    result = _select_and_trade(run_kind, portfolio_ids)
    _last_result = result
    _record_journal(run_kind, result)
    return result


def _select_for_portfolio(portfolio_id: str, candidates: list[dict], errors: list[str]) -> dict:
    """Run selection/sizing for one portfolio's own candidates against its own
    equity/cash — fully independent of every other portfolio."""
    try:
        equity, cash = _get_equity_cash(portfolio_id)
    except Exception as e:
        errors.append(f"{portfolio_id}: {e}")
        return {"selections": [], "candidates": 0, "eligible": 0, "openPositions": 0}

    open_syms = _open_plan_symbols(portfolio_id)
    own_candidates = [c for c in candidates if c["portfolioId"] == portfolio_id]

    if equity <= 0:
        return {
            "selections": [], "candidates": len(own_candidates), "eligible": 0,
            "equity": round(equity, 2), "cash": round(cash, 2),
            "openPositions": len(open_syms), "skipped": "zero_equity",
        }

    if cash / equity < MIN_CASH_PCT:
        log.info("auto-selector[%s]: skipping — cash %.1f%% below %.0f%% floor",
                 portfolio_id, cash / equity * 100, MIN_CASH_PCT * 100)
        return {
            "selections": [], "candidates": len(own_candidates), "eligible": 0,
            "equity": round(equity, 2), "cash": round(cash, 2),
            "openPositions": len(open_syms), "skipped": "low_cash",
        }

    instance_syms = _enabled_instance_symbols(portfolio_id)
    blocked = open_syms | instance_syms
    eligible = [c for c in own_candidates if c["symbol"] not in blocked]

    log.info("auto-selector[%s]: %d candidates, %d eligible, %d open positions",
             portfolio_id, len(own_candidates), len(eligible), len(open_syms))

    if not eligible:
        return {
            "selections": [], "candidates": len(own_candidates), "eligible": 0,
            "equity": round(equity, 2), "cash": round(cash, 2),
            "openPositions": len(open_syms),
        }

    # Limit batch size to what cash can realistically fund (rough check; engine enforces exactly)
    eligible = eligible[:50]  # safety cap — prevents runaway instance creation on first run

    live: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_fetch_live, c["symbol"]): c["symbol"] for c in eligible}
        for fut in as_completed(futures):
            live[futures[fut]] = fut.result()

    # No balancing needed — this portfolio only ever spends its own cash, up to its
    # own limits. Just take candidates best-score-first (already sorted by caller).
    selections: list[dict] = []
    for c in eligible:
        sym = c["symbol"]
        ld = live.get(sym, {})
        if not ld:
            errors.append(f"{sym}: no live data")
            continue
        price, atr = ld["price"], ld["atr"]
        smart_buy = c.get("smartBuy", False)
        allocation = _size_position(equity, price, atr, smart_buy)
        if allocation < price:
            errors.append(f"{sym}: ${allocation:.0f} allocation can't buy 1 share at ${price:.2f}")
            continue

        strategy = _pick_strategy(c.get("signalType", "technical"), c.get("rsi"))
        params   = {"buyBelow": SMART_BUY_RSI_ENTRY} if smart_buy else {}
        thesis   = _selection_thesis(c)

        try:
            iid = store.create_instance(
                sym, strategy, params, allocation, source_tags=c.get("sources", []),
                selection_thesis=thesis, selection_snapshot=_selection_snapshot(c),
                portfolio_id=portfolio_id,
            )
            sel = {
                "symbol":          sym,
                "instanceId":      iid,
                "portfolioId":     portfolio_id,
                "strategy":        strategy,
                "allocationUsd":   allocation,
                "signalType":      c.get("signalType"),
                "sources":         c.get("sources", []),
                "smartBuy":        smart_buy,
                "compositeScore":  round(_candidate_score(c), 2),
                "price":           price,
                "atr":             atr,
                "selectionThesis": thesis,
            }
            if smart_buy:
                sel["investorQualityScore"] = c.get("investorQualityScore")
                sel["avgAnnualizedGain"]    = c.get("avgAnnualizedGain")
                sel["maxAnnualizedGain"]    = c.get("maxAnnualizedGain")
                sel["buyCount"]             = c.get("buyCount")
            selections.append(sel)
            log.info("auto-selector: instance %d %s %s $%.0f (score=%.1f) portfolio=%s",
                     iid, sym, strategy, allocation, sel["compositeScore"], portfolio_id)
        except Exception as e:
            errors.append(f"{sym}: {e}")

    return {
        "selections": selections, "candidates": len(own_candidates), "eligible": len(eligible),
        "equity": round(equity, 2), "cash": round(cash, 2), "openPositions": len(open_syms),
    }


def _select_and_trade(run_kind: str, portfolio_ids: list[str] | None = None) -> dict:
    ts = time.time()
    errors: list[str] = []

    _run_watchlist_scan()
    _ensure_smart_buys_fresh()

    candidates = _gather_candidates()
    candidates.sort(key=_candidate_score, reverse=True)

    portfolios = store.list_portfolios()
    if portfolio_ids is not None:
        portfolios = [p for p in portfolios if p["id"] in portfolio_ids]
    selections: list[dict] = []
    per_portfolio: dict[str, dict] = {}
    for p in portfolios:
        outcome = _select_for_portfolio(p["id"], candidates, errors)
        selections.extend(outcome.pop("selections"))
        per_portfolio[p["id"]] = outcome

    skip_reasons = {pid: v["skipped"] for pid, v in per_portfolio.items() if v.get("skipped")}
    overall_skipped = (
        "; ".join(f"{pid}: {r}" for pid, r in skip_reasons.items())
        if skip_reasons and len(skip_reasons) == len(portfolios) else None
    )

    # Trigger engine to evaluate newly created instances — one combined run covers
    # every portfolio (evaluate_instance routes each instance to its own portfolio's
    # broker internally, and marks every portfolio to market at the end).
    engine_result: dict = {}
    if selections:
        try:
            engine_result = run_engine(run_kind, portfolio_ids=portfolio_ids)
        except Exception as e:
            errors.append(f"engine run: {e}")
            log.exception("auto-selector engine run failed")

    portfolios_final = engine_result.get("portfolios") or {
        pid: {"equity": v.get("equity", 0), "cash": v.get("cash", 0)} for pid, v in per_portfolio.items()
    }
    final_equity = engine_result["equity"] if "equity" in engine_result else round(
        sum(v.get("equity", 0) for v in per_portfolio.values()), 2
    )
    final_cash = engine_result["cash"] if "cash" in engine_result else round(
        sum(v.get("cash", 0) for v in per_portfolio.values()), 2
    )

    result = {
        "selected":      len(selections),
        "candidates":    len(candidates),
        "eligible":      sum(v.get("eligible", 0) for v in per_portfolio.values()),
        "selections":    selections,
        "errors":        errors,
        "skipped":       overall_skipped,
        "portfolios":    portfolios_final,
        "equity":        round(final_equity, 2),
        "cash":          round(final_cash, 2),
        "openPositions": sum(v.get("openPositions", 0) for v in per_portfolio.values()),
        "engineResult":  engine_result,
        "ts":            ts,
    }
    log.info("auto-selector done: selected=%d errors=%d", len(selections), len(errors))
    return result

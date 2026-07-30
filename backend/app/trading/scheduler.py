"""Twice-daily engine runs: after close (16:00 ET) and mid-morning (10:00 ET),
Monday-Friday. Jobs only fire while the backend process is running; the UI
shows the last run so missed runs are visible.

Selection ("auto_select_*" jobs) runs 5 min after each paper-engine pass.
run_auto_selection() is self-contained for the watchlist scan — it scans the
watchlist itself before gathering candidates — but the smart-buy alert refresh
is still forced explicitly here first as a deterministic guarantee: relying
solely on run_auto_selection()'s internally TTL-gated refresh (congress.py's
_UNIVERSE_TTL) would make freshness depend on how that TTL happens to compare
to the 10:05/16:05 job gap, rather than always refreshing every cycle.

Stop-loss/take-profit/time-stop exits (exits.check_exits) additionally run
every 15 min during the regular equity session (9:30 AM-4:00 PM ET) so a level
breach is caught close to when it happens rather than only at the 10:00/16:00
engine runs. A breach outside that window (pre/post-market) just waits — the
next in-window check (or the 10:00/16:00 run) is what fills it, same as a
real day-session-only stop/limit order.
"""
from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..data import cot as cot_data
from ..routers.congress import get_cached_smart_buys
from ..routers.scan import record_smart_buys
from . import exits
from .auto_selector import run_auto_selection
from .engine import run_engine

log = logging.getLogger("paper.scheduler")

scheduler = AsyncIOScheduler(timezone="America/New_York")

_NY_TZ        = ZoneInfo("America/New_York")
_MARKET_OPEN  = dtime(9, 30)
_MARKET_CLOSE = dtime(16, 0)


def _market_is_open() -> bool:
    """Regular US equity session: 9:30 AM-4:00 PM ET, Monday-Friday. Doesn't
    account for exchange holidays — same limitation as every other job here,
    which also just runs Mon-Fri with no holiday calendar."""
    now = datetime.now(_NY_TZ)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return _MARKET_OPEN <= now.time() < _MARKET_CLOSE


def _run(kind: str) -> None:
    try:
        result = run_engine(kind)
        log.info("scheduled %s run finished: %s", kind, result["results"])
    except Exception:
        log.exception("scheduled %s run failed", kind)


def _smart_buy_scan() -> None:
    try:
        alerts = get_cached_smart_buys()
        inserted = record_smart_buys(alerts)
        log.info("smart-buy scan: %d contrarian tickers found, %d new alerts", len(alerts), inserted)
    except Exception:
        log.exception("smart-buy scan failed")


def _auto_select(kind: str) -> None:
    try:
        result = run_auto_selection(kind)
        log.info("auto-select: selected=%d candidates=%d errors=%d",
                 result.get("selected", 0), result.get("candidates", 0), len(result.get("errors", [])))
    except Exception:
        log.exception("auto-select failed")


def _select(kind: str) -> None:
    """Refresh smart-buy alerts, then run the auto-selector — which scans the
    watchlist itself as part of candidate gathering."""
    _smart_buy_scan()
    _auto_select(kind)


def _check_exits_intraday() -> None:
    """Enforce stop/target/time-stop levels intraday. No-op outside market hours —
    a breach that happens after hours just sits until the next in-window check."""
    if not _market_is_open():
        return
    try:
        result = exits.check_exits()
        if result["exited"] or result["errors"]:
            log.info("intraday exit check: %d exited, %d errors",
                     len(result["exited"]), len(result["errors"]))
    except Exception:
        log.exception("intraday exit check failed")


def _cot_refresh() -> None:
    try:
        result = cot_data.refresh(years_back=3)
        log.info("COT refresh complete: added=%d errors=%s", result["added"], result["errors"])
    except Exception:
        log.exception("COT refresh failed")


def start() -> None:
    scheduler.add_job(
        _run, CronTrigger(day_of_week="mon-fri", hour=16, minute=0),
        args=["close"], id="paper_close", replace_existing=True,
    )
    scheduler.add_job(
        _run, CronTrigger(day_of_week="mon-fri", hour=10, minute=0),
        args=["open"], id="paper_open", replace_existing=True,
    )
    # Refresh smart-buy alerts, then auto-select (which scans the watchlist itself),
    # 5 min after each paper run. Job id keeps the "auto_select" prefix so
    # /api/scan/auto-select/status's job filter still matches.
    scheduler.add_job(
        _select, CronTrigger(day_of_week="mon-fri", hour=10, minute=5),
        id="auto_select_open", replace_existing=True, args=["open"],
    )
    scheduler.add_job(
        _select, CronTrigger(day_of_week="mon-fri", hour=16, minute=5),
        id="auto_select_close", replace_existing=True, args=["close"],
    )
    # CFTC COT data is released every Friday at 3:30 PM ET; download at 4:15 PM
    scheduler.add_job(
        _cot_refresh, CronTrigger(day_of_week="fri", hour=16, minute=15),
        id="cot_refresh", replace_existing=True,
    )
    # Intraday stop/target/time-stop enforcement, every 15 min; _market_is_open()
    # inside the job makes the actual gating precise (9:00/9:15 fire but no-op).
    scheduler.add_job(
        _check_exits_intraday, CronTrigger(day_of_week="mon-fri", hour="9-15", minute="*/15"),
        id="check_exits_intraday", replace_existing=True,
    )
    scheduler.start()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def job_info() -> list[dict]:
    if not scheduler.running:
        return []
    return [
        {"id": j.id, "nextRun": j.next_run_time.isoformat() if j.next_run_time else None}
        for j in scheduler.get_jobs()
    ]

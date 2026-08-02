"""One-time backfill: reconstruct approximate historical equity curves for each
per-strategy portfolio (smart_buy, technical_sustained), covering the period
before today's split — when only the pooled account's history existed.

Individual portfolios only have REAL equity_snapshots starting from the moment
they were created by scripts/seed_portfolios.py. Before that, a bucket never
had its own separately-tracked cash — only equity_snapshots_legacy (the old
pooled curve) exists for that period. This script reconstructs each bucket's
OWN historical curve as follows:

  1. Solve for that bucket's cash at account inception, using the fact that
     its recorded starting_cash (captured at seed time) must equal
     cash_inception + net cash delta of all of that bucket's own fills before
     the split:
         cash_inception = starting_cash - net_pre_split_fill_delta

  2. Walk that bucket's pre-split fills forward chronologically to get cash
     and net position (qty per symbol) as of every historical trading day.

  3. Mark held positions to that day's historical closing price (fetched per
     symbol via yfinance/Polygon) to get equity(day) = cash(day) + sum(qty *
     close_price).

  4. Write one row per trading day into equity_snapshots (the same table the
     live per-portfolio tracker now writes to, for ts < the portfolio's real
     created_at) so the frontend picks it up automatically — no frontend
     changes needed.

This is an approximation, not a re-derivation of history: it uses daily closes
instead of the intraday marks the real system used, and treats each bucket's
cash as if it existed separately since account inception (it didn't — the
account was pooled). It is cross-validated against the real pooled
equity_snapshots_legacy curve (reconstructed smart_buy + reconstructed
technical_sustained should track close to the real pooled total on any given
day) before being applied.

Usage:
    python scripts/reconstruct_bucket_equity.py                # dry run — prints plan + validation only
    python scripts/reconstruct_bucket_equity.py --apply         # writes rows to equity_snapshots
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import client as data_client  # noqa: E402

BUCKETS = ["smart_buy", "technical_sustained"]
CANDLE_PERIOD = "3mo"


def _price_lookup(candles: list[dict]):
    """Returns fn(ts) -> most recent close at or before ts (or the earliest close
    if ts predates all candles)."""
    times = [c["time"] for c in candles]
    closes = [c["close"] for c in candles]

    def lookup(ts: float) -> float | None:
        if not times:
            return None
        if ts < times[0]:
            return closes[0]
        idx = 0
        for i, t in enumerate(times):
            if t <= ts:
                idx = i
            else:
                break
        return closes[idx]

    return lookup


def reconstruct(conn: sqlite3.Connection) -> dict:
    portfolios = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM portfolios")}
    legacy_rows = [dict(r) for r in conn.execute(
        "SELECT ts, equity FROM equity_snapshots_legacy ORDER BY ts"
    )]
    account_inception_ts = legacy_rows[0]["ts"] if legacy_rows else None

    spy = data_client.get_history("SPY", period=CANDLE_PERIOD, interval="1d")
    calendar_times = [c["time"] for c in spy["candles"]]

    result: dict[str, dict] = {}
    warnings: list[str] = []

    for pid in BUCKETS:
        port = portfolios[pid]
        created_at = port["created_at"]
        starting_cash = port["starting_cash"]

        fills = [dict(r) for r in conn.execute(
            "SELECT * FROM fills WHERE portfolio_id=? AND ts < ? ORDER BY ts",
            (pid, created_at),
        )]
        if not fills:
            warnings.append(f"{pid}: no pre-split fills, skipping reconstruction")
            continue

        net_delta = 0.0
        for f in fills:
            amt = f["qty"] * f["price"]
            net_delta += amt if f["side"] in ("sell", "short") else -amt
        cash_inception = starting_cash - net_delta

        symbols = sorted({f["symbol"] for f in fills})
        price_lookups: dict[str, object] = {}
        for sym in symbols:
            try:
                hist = data_client.get_history(sym, period=CANDLE_PERIOD, interval="1d")
                price_lookups[sym] = _price_lookup(hist["candles"])
            except Exception as e:
                warnings.append(f"{pid}/{sym}: price history fetch failed ({e}); "
                                 f"will fall back to last fill price")
                price_lookups[sym] = None

        fill_idx = 0
        cash = cash_inception
        qty: dict[str, float] = {}
        last_fill_price: dict[str, float] = {}
        points = []

        for day_ts in calendar_times:
            if account_inception_ts is not None and day_ts < account_inception_ts:
                continue
            if day_ts >= created_at:
                break
            while fill_idx < len(fills) and fills[fill_idx]["ts"] <= day_ts:
                f = fills[fill_idx]
                amt = f["qty"] * f["price"]
                if f["side"] in ("buy", "cover"):
                    cash -= amt
                    qty[f["symbol"]] = qty.get(f["symbol"], 0.0) + f["qty"]
                else:
                    cash += amt
                    qty[f["symbol"]] = qty.get(f["symbol"], 0.0) - f["qty"]
                last_fill_price[f["symbol"]] = f["price"]
                fill_idx += 1

            equity = cash
            for sym, q in qty.items():
                if abs(q) < 1e-9:
                    continue
                lookup = price_lookups.get(sym)
                price = lookup(day_ts) if lookup else None
                if price is None:
                    price = last_fill_price.get(sym, 0.0)
                equity += q * price

            points.append({"ts": day_ts, "equity": round(equity, 2), "cash": round(cash, 2)})

        # apply any remaining fills that occurred after the last calendar day
        # but still before created_at (keeps cash reconciliation exact)
        while fill_idx < len(fills):
            f = fills[fill_idx]
            amt = f["qty"] * f["price"]
            if f["side"] in ("buy", "cover"):
                cash -= amt
            else:
                cash += amt
            fill_idx += 1

        result[pid] = {
            "points": points,
            "cash_inception": round(cash_inception, 2),
            "final_cash_check": round(cash, 2),
            "starting_cash_real": starting_cash,
            "n_fills": len(fills),
            "n_symbols": len(symbols),
        }

    return {"portfolios": result, "warnings": warnings, "legacy": legacy_rows}


def _nearest(rows: list[dict], ts: float) -> dict | None:
    if not rows:
        return None
    return min(rows, key=lambda r: abs(r["ts"] - ts))


def validate(recon: dict) -> list[str]:
    lines = []
    legacy = recon["legacy"]
    ports = recon["portfolios"]
    if len(ports) < 2:
        lines.append("Not enough reconstructed portfolios to cross-validate against pooled history.")
        return lines

    all_ts = sorted({p["ts"] for pts in ports.values() for p in pts["points"]})
    if not all_ts:
        lines.append("No reconstructed points to validate.")
        return lines
    sample_ts = [all_ts[i] for i in range(0, len(all_ts), max(1, len(all_ts) // 6))]

    lines.append(f"{'date (ts)':>14}  {'recon sum':>12}  {'real pooled':>12}  {'diff':>10}  {'diff%':>7}")
    for ts in sample_ts:
        recon_sum = 0.0
        have_all = True
        for pid, data in ports.items():
            pt = _nearest(data["points"], ts)
            if pt is None or abs(pt["ts"] - ts) > 2 * 86400:
                have_all = False
                break
            recon_sum += pt["equity"]
        if not have_all:
            continue
        real_pt = _nearest(legacy, ts)
        real_equity = real_pt["equity"] if real_pt else None
        if real_equity is None:
            continue
        diff = recon_sum - real_equity
        diff_pct = (diff / real_equity * 100) if real_equity else 0.0
        lines.append(f"{ts:>14.0f}  {recon_sum:>12,.2f}  {real_equity:>12,.2f}  {diff:>10,.2f}  {diff_pct:>6.2f}%")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="path to paper.db (default: backend/data/paper.db)")
    ap.add_argument("--apply", action="store_true", help="actually write rows (default: dry run)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(__file__).resolve().parents[1] / "data" / "paper.db"
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    recon = reconstruct(conn)

    if recon["warnings"]:
        print("\nWarnings:")
        for w in recon["warnings"]:
            print(f"  {w}")

    print("\nPer-portfolio reconstruction summary:")
    for pid, data in recon["portfolios"].items():
        print(f"\n  {pid}")
        print(f"    pre-split fills used:  {data['n_fills']} across {data['n_symbols']} symbols")
        print(f"    reconstructed points:  {len(data['points'])}")
        print(f"    cash at inception:     {data['cash_inception']:,.2f}")
        print(f"    cash after all fills:  {data['final_cash_check']:,.2f}  (real starting_cash: {data['starting_cash_real']:,.2f})")
        if data["points"]:
            p0, pN = data["points"][0], data["points"][-1]
            print(f"    first point: equity={p0['equity']:,.2f}  last point: equity={pN['equity']:,.2f}")
        cash_match = abs(data["final_cash_check"] - data["starting_cash_real"]) < 0.01
        print(f"    cash ledger reconciles exactly: {'YES' if cash_match else 'NO -- DO NOT APPLY'}")

    print("\nCross-validation vs. real pooled equity_snapshots_legacy curve:")
    for line in validate(recon):
        print("  " + line)

    if not args.apply:
        print("\nDry run only — no changes written. Re-run with --apply to commit.")
        return

    bad = [pid for pid, d in recon["portfolios"].items()
           if abs(d["final_cash_check"] - d["starting_cash_real"]) >= 0.01]
    if bad:
        print(f"\nRefusing to apply: cash ledger mismatch for {bad}.")
        return

    print("\nApplying...")
    n_written = 0
    for pid, data in recon["portfolios"].items():
        for p in data["points"]:
            conn.execute(
                "INSERT OR IGNORE INTO equity_snapshots (portfolio_id, ts, equity, cash) VALUES (?, ?, ?, ?)",
                (pid, p["ts"], p["equity"], p["cash"]),
            )
            n_written += 1
    conn.commit()
    print(f"Done. Wrote {n_written} historical rows across {len(recon['portfolios'])} portfolios.")


if __name__ == "__main__":
    main()

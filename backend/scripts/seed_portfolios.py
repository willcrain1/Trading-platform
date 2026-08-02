"""One-time seeding script: split the pooled paper account into two real
portfolios (smart_buy, technical_sustained), each starting capital proportional
to that bucket's own contribution so far.

Each bucket's currently-open positions carry over untouched (same shares, same
entry price — nothing is liquidated), so the only thing to actually split is the
account's real remaining cash:

    cash_share(B) = real_pooled_cash * (own_pnl(B) / total_pnl)
    own_pnl(B)    = realized_pnl(B) + unrealized_pnl(B)
    starting_cash(B) = cash_share(B)

An earlier version of this script used a fixed "$10,000 base + P&L" formula, which
produced a NEGATIVE starting cash for a bucket that had more capital currently
deployed in open positions than an even split could support — a portfolio can't
start negative. Splitting the real remaining cash proportionally (rather than an
arbitrary fixed base) guarantees both portfolios start non-negative, and still
implements "proportional to contribution": the bucket that's earned more of the
account's P&L gets more of what's left to trade with, while every open position
carries over exactly as it already was. Falls back to an even cash split if
total_pnl <= 0 (can't proportion by a non-positive total).

Usage:
    python scripts/seed_portfolios.py                 # dry run — prints the plan only
    python scripts/seed_portfolios.py --db path/to.db  # dry run against a specific DB (e.g. a copy)
    python scripts/seed_portfolios.py --apply          # actually writes to the live DB

Always run the dry run against a COPY of paper.db first and check the numbers
before using --apply on the real file.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data import client as data_client  # noqa: E402

BUCKETS = [
    ("smart_buy", "Smart Buy"),
    ("technical_sustained", "Technical/Sustained"),
]


def _bucket_of(source_tags_json: str | None) -> str:
    import json
    tags = json.loads(source_tags_json) if source_tags_json else None
    return "smart_buy" if tags and "smart_buy" in tags else "technical_sustained"


def compute_plan(conn: sqlite3.Connection) -> dict[str, dict]:
    conn.row_factory = sqlite3.Row

    closed = conn.execute("""
        SELECT p.*, i.source_tags FROM trade_plans p
        LEFT JOIN instances i ON i.id = p.instance_id
        WHERE p.status = 'closed'
    """).fetchall()
    open_plans = conn.execute("""
        SELECT p.*, i.source_tags FROM trade_plans p
        LEFT JOIN instances i ON i.id = p.instance_id
        WHERE p.status = 'open'
    """).fetchall()

    realized: dict[str, float] = {"smart_buy": 0.0, "technical_sustained": 0.0}
    for p in closed:
        pid = p["portfolio_id"] or _bucket_of(p["source_tags"])
        realized[pid] = realized.get(pid, 0.0) + (p["realized_pnl"] or 0.0)

    unrealized: dict[str, float] = {"smart_buy": 0.0, "technical_sustained": 0.0}
    position_value: dict[str, float] = {"smart_buy": 0.0, "technical_sustained": 0.0}
    price_errors: list[str] = []
    for p in open_plans:
        pid = p["portfolio_id"] or _bucket_of(p["source_tags"])
        try:
            last = data_client.get_quote(p["symbol"])["last"]
        except Exception as e:
            price_errors.append(f"{p['symbol']}: {e}")
            last = p["entry_price"]  # fall back to entry price (0 unrealized) rather than crash
        qty = p["qty"]
        direction = p["direction"] or "long"
        value = qty * last
        cost = qty * p["entry_price"]
        pnl = (value - cost) if direction == "long" else (cost - value)
        unrealized[pid] = unrealized.get(pid, 0.0) + pnl
        position_value[pid] = position_value.get(pid, 0.0) + value

    real_pooled_cash = conn.execute("SELECT cash FROM account WHERE id = 1").fetchone()[0]

    own_pnl = {
        pid: realized.get(pid, 0.0) + unrealized.get(pid, 0.0)
        for pid, _ in BUCKETS
    }
    total_pnl = sum(own_pnl.values())

    plan: dict[str, dict] = {}
    for pid, label in BUCKETS:
        if total_pnl > 0:
            cash_share = real_pooled_cash * (own_pnl[pid] / total_pnl)
        else:
            cash_share = real_pooled_cash / len(BUCKETS)  # fallback: even split
        plan[pid] = {
            "label": label,
            "realizedPnl": round(realized.get(pid, 0.0), 2),
            "unrealizedPnl": round(unrealized.get(pid, 0.0), 2),
            "openPositionValue": round(position_value.get(pid, 0.0), 2),
            "startingCash": round(cash_share, 2),
            "targetTotalValue": round(cash_share + position_value.get(pid, 0.0), 2),
        }
    plan["_errors"] = price_errors
    plan["_realPooledCash"] = real_pooled_cash
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="path to paper.db (default: backend/data/paper.db)")
    ap.add_argument("--apply", action="store_true", help="actually write the portfolios (default: dry run)")
    args = ap.parse_args()

    db_path = Path(args.db) if args.db else Path(__file__).resolve().parents[1] / "data" / "paper.db"
    print(f"Using DB: {db_path}")
    conn = sqlite3.connect(db_path)
    plan = compute_plan(conn)

    errors = plan.pop("_errors")
    real_pooled_cash = plan.pop("_realPooledCash")
    if errors:
        print("\nQuote lookup errors (fell back to entry price, 0 unrealized for that position):")
        for e in errors:
            print(f"  {e}")

    print(f"\nReal remaining pooled cash (the only thing actually being split): {real_pooled_cash:,.2f}")
    print("\nSeeding plan:")
    total = 0.0
    total_cash = 0.0
    for pid, label in BUCKETS:
        p = plan[pid]
        print(f"\n  {label} ({pid})")
        print(f"    realized P&L:        {p['realizedPnl']:>12,.2f}")
        print(f"    unrealized P&L:      {p['unrealizedPnl']:>12,.2f}")
        print(f"    open position value: {p['openPositionValue']:>12,.2f}")
        print(f"    -> starting cash:      {p['startingCash']:>10,.2f}")
        print(f"    -> target total value: {p['targetTotalValue']:>10,.2f}")
        if p["startingCash"] < 0:
            print("    !! NEGATIVE starting cash — do not apply this plan.")
        total += p["targetTotalValue"]
        total_cash += p["startingCash"]

    print(f"\n  Sum of both portfolios' starting cash: {total_cash:,.2f} (should match real pooled cash above)")
    print(f"  Sum of both portfolios' target total value: {total:,.2f}")
    print("  Compare this to the current live /api/paper/account equity — should match within a cent.")

    if any(plan[pid]["startingCash"] < 0 for pid, _ in BUCKETS):
        print("\nRefusing to apply: at least one portfolio would start with negative cash.")
        return

    if not args.apply:
        print("\nDry run only — no changes written. Re-run with --apply to commit.")
        return

    print("\nApplying...")
    for pid, label in BUCKETS:
        p = plan[pid]
        # starting_cash is the pure cash reserve (what a future reset restores cash
        # to); starting_equity is the day-1 TOTAL value baseline (cash + the carried-
        # over open positions) used for totalPnl/totalPnlPct — without this, P&L% would
        # be measured against cash alone and read as a nonsensical five-figure percent.
        conn.execute(
            "INSERT INTO portfolios (id, label, cash, starting_cash, starting_equity, created_at, enabled)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)"
            " ON CONFLICT(id) DO UPDATE SET label=excluded.label, cash=excluded.cash,"
            " starting_cash=excluded.starting_cash, starting_equity=excluded.starting_equity",
            (pid, label, p["startingCash"], p["startingCash"], p["targetTotalValue"], time.time()),
        )
    conn.commit()
    print("Done. Portfolios seeded.")


if __name__ == "__main__":
    main()

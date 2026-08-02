"""SQLite persistence for the paper trading engine.

Separate database from the market-data cache so clearing one never touches
the other. Single implicit account. All monetary values are USD floats.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "paper.db"

STARTING_CASH = 20_000.0

# The two portfolio-balance buckets the auto-selector splits picks between, and that
# paper-trading stats can be filtered by. Shared constant so bucket strings can't drift
# out of sync between auto_selector.py and the stats/filtering endpoints.
BUCKET_SMART_BUY           = "smart_buy"
BUCKET_TECHNICAL_SUSTAINED = "technical_sustained"
BUCKET_CRYPTO              = "crypto"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def bucket_of_tags(tags: list[str] | None) -> str:
    """Classify instance/plan source_tags into the balance buckets: Smart Buy, Crypto,
    or everything else (technical/sustained/congress/smart_universe). Fallback path
    only — callers that create instances/plans normally stamp portfolio_id explicitly
    at creation time, so this is just a safety net if that's ever skipped."""
    if tags and "crypto" in tags:
        return BUCKET_CRYPTO
    return BUCKET_SMART_BUY if tags and "smart_buy" in tags else BUCKET_TECHNICAL_SUSTAINED

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash REAL NOT NULL,
  starting_cash REAL NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolios (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  cash REAL NOT NULL,
  starting_cash REAL NOT NULL,
  starting_equity REAL,
  created_at REAL NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  symbol TEXT NOT NULL,
  strategy TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  allocation_usd REAL NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at REAL NOT NULL,
  source_tags TEXT,
  selection_thesis TEXT,
  selection_snapshot TEXT,
  portfolio_id TEXT
);
CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id INTEGER,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('buy','sell','short','cover')),
  qty REAL NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('proposed','approved','vetoed','filled','error')),
  run_kind TEXT NOT NULL,
  note TEXT,
  proposed_at REAL NOT NULL,
  filled_at REAL,
  fill_price REAL,
  portfolio_id TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  order_id INTEGER PRIMARY KEY,
  verdict TEXT NOT NULL,
  size_factor REAL NOT NULL,
  rationale TEXT NOT NULL,
  model TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL,
  qty REAL NOT NULL,
  price REAL NOT NULL,
  ts REAL NOT NULL,
  portfolio_id TEXT
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
  portfolio_id TEXT NOT NULL,
  ts REAL NOT NULL,
  equity REAL NOT NULL,
  cash REAL NOT NULL,
  PRIMARY KEY (portfolio_id, ts)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  proposed INTEGER NOT NULL DEFAULT 0,
  filled INTEGER NOT NULL DEFAULT 0,
  vetoed INTEGER NOT NULL DEFAULT 0,
  errors TEXT
);
CREATE TABLE IF NOT EXISTS trade_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  instance_id INTEGER,
  symbol TEXT NOT NULL,
  entry_order_id INTEGER NOT NULL,
  qty REAL NOT NULL,
  entry_price REAL NOT NULL,
  opened_at REAL NOT NULL,
  stop_loss REAL NOT NULL,
  take_profit REAL NOT NULL,
  max_hold_days INTEGER NOT NULL,
  exit_plan TEXT NOT NULL,
  thesis TEXT NOT NULL,
  levels_source TEXT NOT NULL CHECK (levels_source IN ('analyst','mechanical')),
  direction TEXT NOT NULL DEFAULT 'long' CHECK (direction IN ('long','short')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
  exit_order_id INTEGER,
  exit_price REAL,
  exit_reason TEXT CHECK (exit_reason IN ('stop_loss','take_profit','time_stop','signal_exit','manual')),
  closed_at REAL,
  realized_pnl REAL,
  realized_pnl_pct REAL,
  portfolio_id TEXT
);
CREATE TABLE IF NOT EXISTS auto_select_runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  ts            REAL NOT NULL,
  run_kind      TEXT NOT NULL,
  candidates    INTEGER NOT NULL DEFAULT 0,
  eligible      INTEGER NOT NULL DEFAULT 0,
  selected      INTEGER NOT NULL DEFAULT 0,
  skipped       TEXT,
  equity        REAL,
  cash          REAL,
  selections    TEXT NOT NULL DEFAULT '[]',
  errors        TEXT NOT NULL DEFAULT '[]',
  bucket_totals TEXT
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent schema migrations for existing databases."""
    # Expand orders.side constraint to include 'short' and 'cover'
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'"
    ).fetchone()
    if row and "'short'" not in row[0]:
        conn.executescript("""
            CREATE TABLE orders_v2 (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              instance_id INTEGER,
              symbol TEXT NOT NULL,
              side TEXT NOT NULL CHECK (side IN ('buy','sell','short','cover')),
              qty REAL NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('proposed','approved','vetoed','filled','error')),
              run_kind TEXT NOT NULL,
              note TEXT,
              proposed_at REAL NOT NULL,
              filled_at REAL,
              fill_price REAL
            );
            INSERT INTO orders_v2 SELECT * FROM orders;
            DROP TABLE orders;
            ALTER TABLE orders_v2 RENAME TO orders;
        """)
        conn.commit()
    # Add direction column to trade_plans if missing
    try:
        conn.execute("ALTER TABLE trade_plans ADD COLUMN direction TEXT NOT NULL DEFAULT 'long'")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    # Add source_tags column to instances if missing
    try:
        conn.execute("ALTER TABLE instances ADD COLUMN source_tags TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # already exists
    # Add selection_thesis / selection_snapshot columns to instances if missing
    for col in ("selection_thesis", "selection_snapshot"):
        try:
            conn.execute(f"ALTER TABLE instances ADD COLUMN {col} TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # already exists

    # ── per-strategy portfolios migration ───────────────────────────────────
    # Split the single shared account into one real portfolio per strategy
    # bucket. No FK enforcement exists anywhere in this DB (no PRAGMA
    # foreign_keys), so plain nullable ADD COLUMN is sufficient — no table
    # rebuilds needed.
    for table in ("instances", "orders", "fills", "trade_plans"):
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN portfolio_id TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # already exists

    try:
        conn.execute("ALTER TABLE portfolios ADD COLUMN starting_equity REAL")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # already exists

    # equity_snapshots used to be one global row per timestamp (PK on ts alone).
    # Old pooled history can't be retroactively attributed to one portfolio, so
    # preserve it read-only under a new name and start a fresh portfolio-scoped
    # table (composite PK) for go-forward history.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(equity_snapshots)").fetchall()]
    if cols and "portfolio_id" not in cols:
        conn.execute("ALTER TABLE equity_snapshots RENAME TO equity_snapshots_legacy")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
              portfolio_id TEXT NOT NULL,
              ts REAL NOT NULL,
              equity REAL NOT NULL,
              cash REAL NOT NULL,
              PRIMARY KEY (portfolio_id, ts)
            )
        """)
        conn.commit()

    # Backfill portfolio_id on existing instances from their source_tags, then
    # cascade to orders/trade_plans (via instance_id) and fills (via order_id).
    # Orphaned rows (deleted/missing instance) default to Technical/Sustained,
    # matching bucket_of_tags(None)'s existing default.
    rows = conn.execute("SELECT id, source_tags FROM instances WHERE portfolio_id IS NULL").fetchall()
    for r in rows:
        tags = json.loads(r["source_tags"]) if r["source_tags"] else None
        conn.execute("UPDATE instances SET portfolio_id = ? WHERE id = ?", (bucket_of_tags(tags), r["id"]))
    conn.execute(f"""
        UPDATE orders SET portfolio_id = COALESCE(
            (SELECT portfolio_id FROM instances WHERE instances.id = orders.instance_id),
            '{BUCKET_TECHNICAL_SUSTAINED}'
        ) WHERE portfolio_id IS NULL
    """)
    conn.execute(f"""
        UPDATE trade_plans SET portfolio_id = COALESCE(
            (SELECT portfolio_id FROM instances WHERE instances.id = trade_plans.instance_id),
            '{BUCKET_TECHNICAL_SUSTAINED}'
        ) WHERE portfolio_id IS NULL
    """)
    conn.execute(f"""
        UPDATE fills SET portfolio_id = COALESCE(
            (SELECT portfolio_id FROM orders WHERE orders.id = fills.order_id),
            '{BUCKET_TECHNICAL_SUSTAINED}'
        ) WHERE portfolio_id IS NULL
    """)
    conn.commit()

    # Seed the two known portfolios if they don't exist yet. Starting cash is
    # 0 here deliberately — the one-time seeding script (scripts/seed_portfolios.py)
    # sets the real starting_cash/cash split based on each bucket's actual
    # realized+unrealized contribution; this is just a safety net so the app
    # never crashes looking up a portfolio row that doesn't exist yet.
    for pid, label in ((BUCKET_SMART_BUY, "Smart Buy"), (BUCKET_TECHNICAL_SUSTAINED, "Technical/Sustained")):
        exists = conn.execute("SELECT 1 FROM portfolios WHERE id = ?", (pid,)).fetchone()
        if exists is None:
            conn.execute(
                "INSERT INTO portfolios (id, label, cash, starting_cash, created_at, enabled)"
                " VALUES (?, ?, 0, 0, ?, 1)",
                (pid, label, time.time()),
            )

    # Crypto has no pooled trading history to carve up (it's a genuinely new
    # strategy), so unlike the two buckets above it gets the real $10,000
    # baseline immediately instead of a zero placeholder awaiting a seeding script.
    if conn.execute("SELECT 1 FROM portfolios WHERE id = ?", (BUCKET_CRYPTO,)).fetchone() is None:
        conn.execute(
            "INSERT INTO portfolios (id, label, cash, starting_cash, starting_equity, created_at, enabled)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)",
            (BUCKET_CRYPTO, "Crypto", DEFAULT_PORTFOLIO_STARTING_CASH, DEFAULT_PORTFOLIO_STARTING_CASH,
             DEFAULT_PORTFOLIO_STARTING_CASH, time.time()),
        )
    conn.commit()


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA)
        _migrate(_conn)
        row = _conn.execute("SELECT 1 FROM account WHERE id = 1").fetchone()
        if row is None:
            _conn.execute(
                "INSERT INTO account (id, cash, starting_cash, created_at) VALUES (1, ?, ?, ?)",
                (STARTING_CASH, STARTING_CASH, time.time()),
            )
        _conn.commit()
    return _conn


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


# ---- account (legacy singleton — superseded by portfolios below) ----

def get_account() -> dict:
    """Legacy pooled-account row. No longer written to; kept only so old data
    remains readable. Use get_portfolio()/list_portfolios() for anything new."""
    with _lock:
        acc = dict(get_conn().execute("SELECT * FROM account WHERE id = 1").fetchone())
    return acc


def set_cash(cash: float) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE account SET cash = ? WHERE id = 1", (cash,))
        conn.commit()


# ---- portfolios (one real capital pool + equity curve per strategy) ----

DEFAULT_PORTFOLIO_STARTING_CASH = 10_000.0


def create_portfolio(portfolio_id: str, label: str,
                     starting_cash: float = DEFAULT_PORTFOLIO_STARTING_CASH,
                     starting_equity: float | None = None) -> None:
    """starting_cash is the pure cash reserve (what reset_portfolio() restores cash
    to). starting_equity is the day-1 TOTAL value baseline (cash + any carried-over
    open positions) used to measure totalPnl/totalPnlPct going forward — defaults to
    starting_cash for the normal case of a brand-new portfolio with no positions yet.
    They diverge when a portfolio is seeded with pre-existing open positions (see
    scripts/seed_portfolios.py), where starting_cash alone would understate the real
    day-1 baseline and produce a nonsensical P&L%.

    starting_cash defaults to $10,000 — the baseline for a genuinely new strategy
    with no trading history to inherit. Pass an explicit value when seeding a
    portfolio out of pre-existing pooled history (e.g. scripts/seed_portfolios.py),
    where the fair starting balance has to be computed instead of assumed."""
    if starting_equity is None:
        starting_equity = starting_cash
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO portfolios (id, label, cash, starting_cash, starting_equity, created_at, enabled)"
            " VALUES (?, ?, ?, ?, ?, ?, 1)"
            " ON CONFLICT(id) DO UPDATE SET label=excluded.label, cash=excluded.cash,"
            " starting_cash=excluded.starting_cash, starting_equity=excluded.starting_equity",
            (portfolio_id, label, starting_cash, starting_cash, starting_equity, time.time()),
        )
        conn.commit()


def get_portfolio(portfolio_id: str) -> dict:
    with _lock:
        row = get_conn().execute("SELECT * FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown portfolio '{portfolio_id}'")
    return dict(row)


def list_portfolios() -> list[dict]:
    with _lock:
        return _rows(get_conn().execute("SELECT * FROM portfolios ORDER BY id"))


def adjust_cash(delta: float, portfolio_id: str, *, min_cash: float | None = None) -> float:
    """Atomically read-modify-write one portfolio's cash by delta under a single lock
    acquisition. Reading and writing inside one `with _lock` (rather than a separate
    read-then-write) closes a lost-update race where two orders filling close together
    could silently drop part of a real cash movement. Raises ValueError (leaving cash
    unchanged) if min_cash is given and the result would fall below it.
    """
    with _lock:
        conn = get_conn()
        row = conn.execute("SELECT cash FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown portfolio '{portfolio_id}'")
        cur_cash = row[0]
        new_cash = cur_cash + delta
        if min_cash is not None and new_cash < min_cash - 1e-6:
            raise ValueError(f"insufficient cash: have {cur_cash:,.2f}, need {-delta:,.2f}")
        conn.execute("UPDATE portfolios SET cash = ? WHERE id = ?", (new_cash, portfolio_id))
        conn.commit()
        return new_cash


def reset_portfolio(portfolio_id: str) -> None:
    """Wipe one portfolio's trading data and restore it to its own starting_cash —
    every other portfolio is untouched."""
    with _lock:
        conn = get_conn()
        row = conn.execute("SELECT starting_cash FROM portfolios WHERE id = ?", (portfolio_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown portfolio '{portfolio_id}'")
        starting_cash = row[0]
        conn.execute(
            "DELETE FROM decisions WHERE order_id IN (SELECT id FROM orders WHERE portfolio_id = ?)",
            (portfolio_id,),
        )
        conn.execute("DELETE FROM fills WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute("DELETE FROM orders WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute("DELETE FROM trade_plans WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute("DELETE FROM instances WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute("DELETE FROM equity_snapshots WHERE portfolio_id = ?", (portfolio_id,))
        conn.execute("UPDATE portfolios SET cash = ? WHERE id = ?", (starting_cash, portfolio_id))
        conn.commit()


# ---- positions ----

def get_positions(portfolio_id: str | None = None) -> list[dict]:
    """Net positions derived from fills. Positive qty = long, negative = short.
    portfolio_id=None blends fills across every portfolio into one net-exposure view
    (used for the combined/all-sources display); pass an explicit id to scope to one
    portfolio's own book (used by that portfolio's broker)."""
    with _lock:
        if portfolio_id is None:
            fills = _rows(get_conn().execute("SELECT * FROM fills ORDER BY ts"))
        else:
            fills = _rows(get_conn().execute(
                "SELECT * FROM fills WHERE portfolio_id = ? ORDER BY ts", (portfolio_id,)
            ))
    pos: dict[str, dict] = {}
    for f in fills:
        p = pos.setdefault(f["symbol"], {"symbol": f["symbol"], "qty": 0.0, "cost": 0.0})
        side = f["side"]
        if side in ("buy", "cover"):
            # Adding shares (opening long or closing short)
            p["cost"] += f["qty"] * f["price"]
            p["qty"] += f["qty"]
        else:
            # Removing shares (closing long or opening short)
            if p["qty"] > 0:
                # Closing a long: remove cost proportionally
                p["cost"] -= (p["cost"] / p["qty"]) * f["qty"]
            else:
                # Opening / adding to a short: track short proceeds as negative cost
                p["cost"] -= f["qty"] * f["price"]
            p["qty"] -= f["qty"]
    out = []
    for p in pos.values():
        if abs(p["qty"]) < 1e-9:
            continue
        out.append(
            {"symbol": p["symbol"], "qty": round(p["qty"], 6),
             "avgCost": round(p["cost"] / p["qty"], 4) if p["qty"] else 0.0}
        )
    return out


# ---- instances ----

def list_instances() -> list[dict]:
    with _lock:
        rows = _rows(get_conn().execute("SELECT * FROM instances ORDER BY id"))
    for r in rows:
        r["params"] = json.loads(r["params"])
        r["enabled"] = bool(r["enabled"])
        r["source_tags"] = json.loads(r["source_tags"]) if r.get("source_tags") else []
        r["selection_snapshot"] = json.loads(r["selection_snapshot"]) if r.get("selection_snapshot") else None
    return rows


def create_instance(symbol: str, strategy: str, params: dict, allocation_usd: float,
                    source_tags: list[str] | None = None, selection_thesis: str | None = None,
                    selection_snapshot: dict | None = None, portfolio_id: str | None = None) -> int:
    """portfolio_id should be passed explicitly by callers that know it (the
    auto-selector always does). Falls back to bucket_of_tags(source_tags) — the
    same classification already used everywhere else — so nothing regresses for
    a caller that doesn't."""
    if portfolio_id is None:
        portfolio_id = bucket_of_tags(source_tags)
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO instances (symbol, strategy, params, allocation_usd, enabled, created_at,"
            " source_tags, selection_thesis, selection_snapshot, portfolio_id)"
            " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (symbol.upper(), strategy, json.dumps(params), allocation_usd, time.time(),
             json.dumps(source_tags) if source_tags else None,
             selection_thesis, json.dumps(selection_snapshot) if selection_snapshot else None,
             portfolio_id),
        )
        conn.commit()
        return int(cur.lastrowid)


def update_instance(instance_id: int, **fields: Any) -> bool:
    allowed = {"enabled", "allocation_usd", "params"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "params":
            v = json.dumps(v)
        if k == "enabled":
            v = int(v)
        sets.append(f"{k} = ?")
        vals.append(v)
    if not sets:
        return False
    with _lock:
        conn = get_conn()
        cur = conn.execute(f"UPDATE instances SET {', '.join(sets)} WHERE id = ?", (*vals, instance_id))
        conn.commit()
        return cur.rowcount > 0


def delete_instance(instance_id: int) -> bool:
    with _lock:
        conn = get_conn()
        cur = conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
        conn.commit()
        return cur.rowcount > 0


# ---- orders / decisions / fills ----

def _instance_portfolio(conn: sqlite3.Connection, instance_id: int | None) -> str:
    """Look up an instance's portfolio_id (must be called with _lock already held).
    Defaults to Technical/Sustained for a missing/deleted instance, matching
    bucket_of_tags(None)'s existing default."""
    if instance_id is not None:
        row = conn.execute("SELECT portfolio_id FROM instances WHERE id = ?", (instance_id,)).fetchone()
        if row and row[0]:
            return row[0]
    return BUCKET_TECHNICAL_SUSTAINED


def _order_portfolio(conn: sqlite3.Connection, order_id: int) -> str:
    """Must be called with _lock already held."""
    row = conn.execute("SELECT portfolio_id FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row and row[0]:
        return row[0]
    return BUCKET_TECHNICAL_SUSTAINED


def create_order(instance_id: int | None, symbol: str, side: str, qty: float,
                 run_kind: str, note: str | None = None) -> int:
    """portfolio_id is derived from the instance automatically — callers don't need
    to pass it (orders always belong to an instance, which already knows its
    portfolio)."""
    with _lock:
        conn = get_conn()
        portfolio_id = _instance_portfolio(conn, instance_id)
        cur = conn.execute(
            "INSERT INTO orders (instance_id, symbol, side, qty, status, run_kind, note, proposed_at,"
            " portfolio_id)"
            " VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?, ?)",
            (instance_id, symbol.upper(), side, qty, run_kind, note, time.time(), portfolio_id),
        )
        conn.commit()
        return int(cur.lastrowid)


def set_order_status(order_id: int, status: str, qty: float | None = None,
                     note: str | None = None) -> None:
    with _lock:
        conn = get_conn()
        if qty is not None:
            conn.execute("UPDATE orders SET status = ?, qty = ? WHERE id = ?", (status, qty, order_id))
        else:
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        if note is not None:
            conn.execute("UPDATE orders SET note = ? WHERE id = ?", (note, order_id))
        conn.commit()


def record_decision(order_id: int, verdict: str, size_factor: float,
                    rationale: str, model: str | None) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO decisions (order_id, verdict, size_factor, rationale, model, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, verdict, size_factor, rationale, model, time.time()),
        )
        conn.commit()


def record_fill(order_id: int, symbol: str, side: str, qty: float, price: float) -> None:
    """portfolio_id is derived from the order automatically."""
    now = time.time()
    with _lock:
        conn = get_conn()
        portfolio_id = _order_portfolio(conn, order_id)
        conn.execute(
            "INSERT INTO fills (order_id, symbol, side, qty, price, ts, portfolio_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_id, symbol.upper(), side, qty, price, now, portfolio_id),
        )
        conn.execute(
            "UPDATE orders SET status = 'filled', filled_at = ?, fill_price = ? WHERE id = ?",
            (now, price, order_id),
        )
        conn.commit()


def list_orders(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _rows(get_conn().execute(
            "SELECT o.*, d.verdict, d.size_factor, d.rationale, d.model"
            " FROM orders o LEFT JOIN decisions d ON d.order_id = o.id"
            " ORDER BY o.id DESC LIMIT ?",
            (limit,),
        ))
    return rows


# ---- trade plans ----

def create_plan(instance_id: int | None, symbol: str, entry_order_id: int, qty: float,
                entry_price: float, stop_loss: float, take_profit: float,
                max_hold_days: int, exit_plan: str, thesis: str,
                levels_source: str, direction: str = "long") -> int:
    """portfolio_id is derived from the instance automatically."""
    with _lock:
        conn = get_conn()
        portfolio_id = _instance_portfolio(conn, instance_id)
        cur = conn.execute(
            "INSERT INTO trade_plans (instance_id, symbol, entry_order_id, qty, entry_price,"
            " opened_at, stop_loss, take_profit, max_hold_days, exit_plan, thesis,"
            " levels_source, direction, portfolio_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (instance_id, symbol.upper(), entry_order_id, qty, entry_price, time.time(),
             stop_loss, take_profit, max_hold_days, exit_plan, thesis, levels_source, direction,
             portfolio_id),
        )
        conn.commit()
        return int(cur.lastrowid)


def open_plans() -> list[dict]:
    with _lock:
        rows = _rows(get_conn().execute(
            "SELECT p.*, i.source_tags, i.selection_thesis, i.selection_snapshot FROM trade_plans p"
            " LEFT JOIN instances i ON i.id = p.instance_id"
            " WHERE p.status = 'open' ORDER BY p.id"
        ))
    for r in rows:
        r["source_tags"] = json.loads(r["source_tags"]) if r.get("source_tags") else []
        r["selection_snapshot"] = json.loads(r["selection_snapshot"]) if r.get("selection_snapshot") else None
    return rows


def close_plan(plan_id: int, exit_order_id: int, exit_price: float, reason: str) -> bool:
    """Close a plan. The WHERE clause requires status='open' so a plan already closed
    by a concurrent call (e.g. check_exits_intraday overlapping a scheduled engine run
    that internally calls check_exits — both fire at :00 past the hour) becomes a safe
    no-op instead of overwriting the real close with stale/duplicate data. Returns
    whether this call actually closed it."""
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT qty, entry_price, direction FROM trade_plans WHERE id = ? AND status = 'open'",
            (plan_id,),
        ).fetchone()
        if row is None:
            return False
        direction = row["direction"] if "direction" in row.keys() else "long"
        if direction == "short":
            # Short profit: entered high, exiting low
            pnl = (row["entry_price"] - exit_price) * row["qty"]
            pnl_pct = (row["entry_price"] / exit_price - 1) * 100 if exit_price else 0.0
        else:
            pnl = (exit_price - row["entry_price"]) * row["qty"]
            pnl_pct = (exit_price / row["entry_price"] - 1) * 100 if row["entry_price"] else 0.0
        conn.execute(
            "UPDATE trade_plans SET status = 'closed', exit_order_id = ?, exit_price = ?,"
            " exit_reason = ?, closed_at = ?, realized_pnl = ?, realized_pnl_pct = ?"
            " WHERE id = ? AND status = 'open'",
            (exit_order_id, exit_price, reason, time.time(),
             round(pnl, 2), round(pnl_pct, 3), plan_id),
        )
        conn.commit()
        return True


def close_plan_untracked(plan_id: int, reason: str = "manual") -> bool:
    """Close a plan whose position is already gone with no matching order. Only two
    legitimate causes: (1) a genuine orphan — the position vanished some other way
    while the plan stayed open, or (2) a concurrent call already closed it correctly
    moments ago (see close_plan()'s docstring) and this is a stale second pass. The
    status='open' guard means case (2) is a no-op — it never overwrites a real close
    with fabricated data. For real case (1), no exit price/order/P&L is invented
    (close_plan() would otherwise price a nonexistent trade at the current quote and
    silently record realized P&L that never actually happened)."""
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "UPDATE trade_plans SET status = 'closed', exit_order_id = NULL, exit_price = NULL,"
            " exit_reason = ?, closed_at = ?, realized_pnl = NULL, realized_pnl_pct = NULL"
            " WHERE id = ? AND status = 'open'",
            (reason, time.time(), plan_id),
        )
        conn.commit()
        return cur.rowcount > 0


def list_plans(status: str = "all", limit: int = 100) -> list[dict]:
    """Plans newest-first, open before closed, joined with the entry order's
    analyst decision and instance source_tags."""
    where = "" if status == "all" else "WHERE p.status = ?"
    args: tuple = () if status == "all" else (status,)
    with _lock:
        rows = _rows(get_conn().execute(
            f"SELECT p.*, d.verdict, d.size_factor, d.rationale, d.model,"
            f" i.source_tags, i.selection_thesis, i.selection_snapshot"
            f" FROM trade_plans p"
            f" LEFT JOIN decisions d ON d.order_id = p.entry_order_id"
            f" LEFT JOIN instances i ON i.id = p.instance_id"
            f" {where}"
            f" ORDER BY (p.status = 'open') DESC, p.id DESC LIMIT ?",
            (*args, limit),
        ))
    for r in rows:
        r["source_tags"] = json.loads(r["source_tags"]) if r.get("source_tags") else []
        r["selection_snapshot"] = json.loads(r["selection_snapshot"]) if r.get("selection_snapshot") else None
    return rows


# ---- equity / runs ----

def snapshot_equity(portfolio_id: str, equity: float, cash: float) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots (portfolio_id, ts, equity, cash) VALUES (?, ?, ?, ?)",
            (portfolio_id, time.time(), equity, cash),
        )
        conn.commit()


def equity_curve(portfolio_id: str) -> list[dict]:
    with _lock:
        return _rows(get_conn().execute(
            "SELECT * FROM equity_snapshots WHERE portfolio_id = ? ORDER BY ts", (portfolio_id,)
        ))


def equity_curve_legacy() -> list[dict]:
    """The old pooled account's equity history, preserved read-only from before the
    per-portfolio split. See _migrate()'s equity_snapshots_legacy rename."""
    with _lock:
        try:
            return _rows(get_conn().execute("SELECT * FROM equity_snapshots_legacy ORDER BY ts"))
        except sqlite3.OperationalError:
            return []  # no legacy table — this DB predates pooling or was never migrated


def start_run(kind: str) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute("INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, time.time()))
        conn.commit()
        return int(cur.lastrowid)


def finish_run(run_id: int, proposed: int, filled: int, vetoed: int, errors: list[str]) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "UPDATE runs SET finished_at = ?, proposed = ?, filled = ?, vetoed = ?, errors = ? WHERE id = ?",
            (time.time(), proposed, filled, vetoed, json.dumps(errors) if errors else None, run_id),
        )
        conn.commit()


def list_runs(limit: int = 20) -> list[dict]:
    with _lock:
        rows = _rows(get_conn().execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)))
    for r in rows:
        r["errors"] = json.loads(r["errors"]) if r["errors"] else []
    return rows


# ---- auto-selector journal ----

def record_auto_select_run(run_kind: str, candidates: int, eligible: int, selected: int,
                           skipped: str | None, equity: float | None, cash: float | None,
                           selections: list[dict], errors: list[str],
                           bucket_totals: dict | None) -> int:
    """Persist one auto-selector invocation (10am/4pm scheduled run or a manual trigger) —
    every call writes an entry regardless of whether anything was actually selected, so
    skipped/empty cycles are visible in the journal too."""
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO auto_select_runs (ts, run_kind, candidates, eligible, selected, skipped,"
            " equity, cash, selections, errors, bucket_totals) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), run_kind, candidates, eligible, selected, skipped, equity, cash,
             json.dumps(selections), json.dumps(errors),
             json.dumps(bucket_totals) if bucket_totals else None),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_auto_select_runs(limit: int = 50) -> list[dict]:
    with _lock:
        rows = _rows(get_conn().execute(
            "SELECT * FROM auto_select_runs ORDER BY id DESC LIMIT ?", (limit,)
        ))
    for r in rows:
        r["selections"] = json.loads(r["selections"]) if r.get("selections") else []
        r["errors"] = json.loads(r["errors"]) if r.get("errors") else []
        r["bucket_totals"] = json.loads(r["bucket_totals"]) if r.get("bucket_totals") else None
    return rows


def reset_account() -> None:
    """Nuclear option: reset every portfolio (wipes each one's trading data, restores
    each to its own starting_cash — see reset_portfolio()) and clears the shared
    runs/auto-select journal. Prefer reset_portfolio(portfolio_id) to reset one
    strategy without touching the others."""
    for p in list_portfolios():
        reset_portfolio(p["id"])
    with _lock:
        conn = get_conn()
        conn.executescript("""
            DELETE FROM runs;
            DELETE FROM auto_select_runs;
        """)
        conn.commit()

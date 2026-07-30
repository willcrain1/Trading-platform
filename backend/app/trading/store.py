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

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def bucket_of_tags(tags: list[str] | None) -> str:
    """Classify instance/plan source_tags into the two balance buckets: Smart Buy vs
    everything else (technical/sustained/congress/smart_universe)."""
    return BUCKET_SMART_BUY if tags and "smart_buy" in tags else BUCKET_TECHNICAL_SUSTAINED

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash REAL NOT NULL,
  starting_cash REAL NOT NULL,
  created_at REAL NOT NULL
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
  selection_snapshot TEXT
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
  fill_price REAL
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
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS equity_snapshots (
  ts REAL PRIMARY KEY,
  equity REAL NOT NULL,
  cash REAL NOT NULL
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
  realized_pnl_pct REAL
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


# ---- account / positions ----

def get_account() -> dict:
    with _lock:
        acc = dict(get_conn().execute("SELECT * FROM account WHERE id = 1").fetchone())
    return acc


def set_cash(cash: float) -> None:
    with _lock:
        conn = get_conn()
        conn.execute("UPDATE account SET cash = ? WHERE id = 1", (cash,))
        conn.commit()


def get_positions() -> list[dict]:
    """Net positions derived from fills. Positive qty = long, negative = short."""
    with _lock:
        fills = _rows(get_conn().execute("SELECT * FROM fills ORDER BY ts"))
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
                    selection_snapshot: dict | None = None) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO instances (symbol, strategy, params, allocation_usd, enabled, created_at,"
            " source_tags, selection_thesis, selection_snapshot)"
            " VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (symbol.upper(), strategy, json.dumps(params), allocation_usd, time.time(),
             json.dumps(source_tags) if source_tags else None,
             selection_thesis, json.dumps(selection_snapshot) if selection_snapshot else None),
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

def create_order(instance_id: int | None, symbol: str, side: str, qty: float,
                 run_kind: str, note: str | None = None) -> int:
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO orders (instance_id, symbol, side, qty, status, run_kind, note, proposed_at)"
            " VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)",
            (instance_id, symbol.upper(), side, qty, run_kind, note, time.time()),
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
    now = time.time()
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT INTO fills (order_id, symbol, side, qty, price, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, symbol.upper(), side, qty, price, now),
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
    with _lock:
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO trade_plans (instance_id, symbol, entry_order_id, qty, entry_price,"
            " opened_at, stop_loss, take_profit, max_hold_days, exit_plan, thesis,"
            " levels_source, direction)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (instance_id, symbol.upper(), entry_order_id, qty, entry_price, time.time(),
             stop_loss, take_profit, max_hold_days, exit_plan, thesis, levels_source, direction),
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


def close_plan(plan_id: int, exit_order_id: int, exit_price: float, reason: str) -> None:
    with _lock:
        conn = get_conn()
        row = conn.execute(
            "SELECT qty, entry_price, direction FROM trade_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if row is None:
            return
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
            " WHERE id = ?",
            (exit_order_id, exit_price, reason, time.time(),
             round(pnl, 2), round(pnl_pct, 3), plan_id),
        )
        conn.commit()


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

def snapshot_equity(equity: float, cash: float) -> None:
    with _lock:
        conn = get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO equity_snapshots (ts, equity, cash) VALUES (?, ?, ?)",
            (time.time(), equity, cash),
        )
        conn.commit()


def equity_curve() -> list[dict]:
    with _lock:
        return _rows(get_conn().execute("SELECT * FROM equity_snapshots ORDER BY ts"))


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
    """Wipe all trading data and restore the account to STARTING_CASH."""
    global _conn
    with _lock:
        conn = get_conn()
        conn.executescript("""
            DELETE FROM fills;
            DELETE FROM decisions;
            DELETE FROM orders;
            DELETE FROM trade_plans;
            DELETE FROM instances;
            DELETE FROM equity_snapshots;
            DELETE FROM runs;
            DELETE FROM auto_select_runs;
        """)
        conn.execute(
            "UPDATE account SET cash = ?, starting_cash = ? WHERE id = 1",
            (STARTING_CASH, STARTING_CASH),
        )
        conn.commit()

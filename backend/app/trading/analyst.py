"""Claude analyst: reviews each mechanically-proposed order before execution.

The mechanical strategy is the tested baseline; the analyst is an overlay that
can veto or downsize a trade based on broader context (ticker signals, macro
regime, dealer positioning). For BUYs it additionally refines the trade plan:
stop-loss / take-profit / max-hold levels (starting from ATR-scaled mechanical
defaults) and a written thesis that is stored with the trade. If the CLI is
unavailable or the call fails, orders are approved at full size and the
mechanical defaults stand, so the engine never blocks on the analyst.

Runs via the local `claude` CLI in headless print mode rather than the
Anthropic API SDK, so review calls draw on the user's Claude subscription
(Pro/Max) usage allowance instead of separate pay-per-token API billing.
Requires `claude auth login` to have been run once on this machine.

CLI flag notes (verified empirically — see git history / session notes):
- `--safe-mode` skips CLAUDE.md/memory/hooks auto-discovery, which otherwise
  balloons cost by loading the whole project's context into every call.
- `--safe-mode` combined with `--system-prompt` hangs the CLI outright (a CLI
  bug, reproduced directly in bash, not specific to subprocess invocation).
  Workaround: put instructions in the user prompt instead of a system prompt.
- `--tools ""` disables tool use so the review is a single pure-text turn.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from ..analysis import gex as gex_mod
from ..analysis import indicators, macro
from ..data import client as data_client
from . import store

CLI_TIMEOUT_SECONDS = 120


def _get_model() -> str:
    """Read the analyst model at call time so UI changes take effect without restart."""
    from ..data import app_config
    return app_config.get_analyst_model()

_BASE_PROPS = {
    "verdict": {"type": "string", "enum": ["approve", "veto"]},
    "size_factor": {"type": "number"},
    "rationale": {"type": "string"},
}

SELL_SCHEMA = {
    "type": "object",
    "properties": _BASE_PROPS,
    "required": ["verdict", "size_factor", "rationale"],
    "additionalProperties": False,
}

BUY_SCHEMA = {
    "type": "object",
    "properties": {
        **_BASE_PROPS,
        "stop_loss": {"type": "number"},
        "take_profit": {"type": "number"},
        "max_hold_days": {"type": "integer"},
        "thesis": {"type": "string"},
        "exit_plan": {"type": "string"},
    },
    "required": ["verdict", "size_factor", "rationale", "stop_loss", "take_profit",
                 "max_hold_days", "thesis", "exit_plan"],
    "additionalProperties": False,
}

COMMON_RULES = """You are a risk-review analyst for a PAPER trading simulator (no real money).
A mechanical, backtested strategy has proposed an order. Your job is a sanity check, not a
second trading system: approve unless the broader context clearly argues against the trade.

Rules:
- verdict "approve" executes the order; "veto" cancels it. Vetoes should be uncommon and
  need a concrete reason (e.g. extreme volatility regime, signal computed on stale data,
  position concentration, macro stress directly contradicting the trade).
- size_factor scales a BUY between 0 and 1 (1 = full size). Use it for moderate concerns
  instead of vetoing.
- rationale: 2-4 sentences a human will read in the order log. Reference the specific data
  points that drove your decision."""

BUY_EXTRAS = """
This is a BUY (opening a LONG position), so you must also set the trade plan:
- stop_loss / take_profit: absolute prices. Start from the mechanicalDefaults in the data
  (stop = entry - 2xATR, target = entry + 3xATR) and only move them when you have a stated
  reason. stop_loss must be BELOW the current price and within 0.5x-4x ATR of it;
  take_profit must be ABOVE the current price.
- max_hold_days: trading days before force-close (time stop), 1-365.
- thesis: 4-8 sentences on why you're entering, what supports the trade, and what invalidates it.
- exit_plan: plain-language summary of all exit triggers (stop, target, time, signal off).

Review the proposed order below and respond."""

SHORT_EXTRAS = """
This is a SHORT SALE (opening a SHORT position). Exit levels are INVERTED vs a long:
- stop_loss: absolute price ABOVE entry (where you cover/exit if wrong).
  Start from mechanicalDefaults (stop = entry + 2xATR). Must be within 0.5x-4x ATR ABOVE entry.
- take_profit: absolute price BELOW entry (where you cover for profit).
  Start from mechanicalDefaults (target = entry - 3xATR). Must be BELOW current price.
- max_hold_days: same logic as longs.
- thesis: 4-8 sentences. Explain what makes this a SHORT setup — bearish trend, failed rally,
  distribution pattern, macro headwind — and what would make you cover early.
- exit_plan: summarize all exit triggers (stop if price rises, target if it falls, time, signal).

Review the proposed order below and respond."""

SELL_EXTRAS = """
This is a SELL closing an existing LONG position: never veto for market-risk reasons,
only for data-quality problems. size_factor is ignored for sells.

Review the proposed order below and respond."""

COVER_EXTRAS = """
This is a BUY-TO-COVER closing an existing SHORT position: never veto for market-risk reasons,
only for data-quality problems. size_factor is ignored for covers.

Review the proposed order below and respond."""


def _fallback(reason: str) -> dict:
    return {"verdict": "approve", "sizeFactor": 1.0,
            "rationale": f"Analyst unavailable ({reason}) — mechanical signal executed unreviewed.",
            "model": None}


def _gather_context(order: dict, instance: dict, signal_context: dict,
                    mechanical_defaults: dict | None) -> dict:
    symbol = order["symbol"]
    ctx: dict = {"proposedOrder": order, "strategy": signal_context, "instance": {
        "allocationUsd": instance["allocation_usd"], "params": instance["params"]}}
    if mechanical_defaults:
        ctx["mechanicalDefaults"] = mechanical_defaults
    try:
        ctx["tickerSignals"] = indicators.compute_signals(
            data_client.get_history(symbol, period="2y")["candles"]
        )
    except Exception as e:
        ctx["tickerSignals"] = f"unavailable: {e}"
    try:
        snap = macro.snapshot()
        ctx["macro"] = {
            "riskComposite": snap["riskComposite"],
            "curve10y3m": snap["curve10y3m"],
            "dispersion": snap.get("dispersion"),
            "assets": [
                {k: a[k] for k in ("symbol", "name", "last", "changePct", "ret20Pct")}
                for a in snap["assets"]
            ],
        }
    except Exception as e:
        ctx["macro"] = f"unavailable: {e}"
    try:
        g = gex_mod.compute_gex(symbol)
        ctx["dealerPositioning"] = {
            k: g[k] for k in ("spot", "netGexTotal", "flipPoint", "maxPain",
                              "putCallOiRatio", "keyLevels", "expiration")
        }
    except Exception:
        ctx["dealerPositioning"] = "no listed options or data unavailable"
    ctx["portfolio"] = {
        "cash": store.get_account()["cash"],
        "positions": store.get_positions(),
    }
    return ctx


def review_order(order: dict, instance: dict, signal_context: dict,
                 mechanical_defaults: dict | None = None) -> dict:
    """Returns {verdict, sizeFactor, rationale, model} plus, for approved BUYs,
    {stop_loss, take_profit, max_hold_days, thesis, exit_plan}. Never raises."""
    model = _get_model()
    claude_bin = shutil.which("claude")
    if not claude_bin:
        return _fallback("claude CLI not found on PATH")

    side = order["side"]
    is_entry = side in ("buy", "short")
    schema = BUY_SCHEMA if is_entry else SELL_SCHEMA
    if side == "buy":
        extras = BUY_EXTRAS
    elif side == "short":
        extras = SHORT_EXTRAS
    elif side == "cover":
        extras = COVER_EXTRAS
    else:
        extras = SELL_EXTRAS
    instructions = COMMON_RULES + extras

    context = _gather_context(order, instance, signal_context, mechanical_defaults)
    prompt = instructions + "\n\n" + json.dumps(context, default=str)

    cmd = [
        claude_bin, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--safe-mode",  # do NOT combine with --system-prompt — hangs the CLI
        "--json-schema", json.dumps(schema),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLI_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return _fallback(f"claude CLI timed out after {CLI_TIMEOUT_SECONDS}s")
    except OSError as e:
        return _fallback(f"failed to launch claude CLI: {e}")

    if proc.returncode != 0:
        return _fallback(f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:200]}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _fallback("claude CLI returned non-JSON output")

    if result.get("is_error"):
        return _fallback(f"claude CLI error: {str(result.get('result'))[:200]}")

    verdict = result.get("structured_output")
    if not verdict:
        return _fallback("no structured_output in claude CLI response")

    try:
        size = min(max(float(verdict["size_factor"]), 0.0), 1.0)
        out = {
            "verdict": verdict["verdict"],
            "sizeFactor": size if verdict["verdict"] == "approve" else 0.0,
            "rationale": verdict["rationale"],
            "model": f"claude-cli:{model}",
        }
        if is_entry:
            for k in ("stop_loss", "take_profit", "max_hold_days", "thesis", "exit_plan"):
                out[k] = verdict.get(k)
        return out
    except (KeyError, TypeError, ValueError) as e:
        return _fallback(f"malformed verdict: {e}")

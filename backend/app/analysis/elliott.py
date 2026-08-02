"""Elliott Wave analysis: a swing (zigzag) detector plus a best-effort,
rule-validated wave-counting pass on top of it.

Elliott Wave is one of the most subjective methods in technical analysis —
professional analysts routinely disagree on the count for the same chart.
What follows is a heuristic: hard structural rules are enforced (a count
that breaks one is rejected outright), and Fibonacci-ratio guidelines are
used only to *score* and rank the surviving valid counts. The result is one
plausible count, not an authoritative read — every response carries a note
saying so, and callers (API responses, the UI) should keep repeating that
framing rather than presenting a count as settled fact.

Two building blocks:
  - `zigzag()`: an ordered, alternating sequence of confirmed swing highs/
    lows, causal by construction (a pivot only appears once price has
    actually reversed past it by an ATR-scaled threshold).
  - `label_waves()`: searches recent windows of that sequence for a valid
    5-wave impulse or 3-wave (ABC) correction, applying Elliott's three hard
    rules and scoring survivors against common Fibonacci retracement/
    extension zones.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr, to_df

# ── hard-rule-adjacent constants ────────────────────────────────────────────
DEFAULT_ATR_MULT = 2.5

# Soft Fibonacci guideline zones/targets — used only for scoring, never to
# reject a count.
_RETRACE_ZONE_WAVE2 = (0.5, 0.786)
_RETRACE_ZONE_WAVE4 = (0.236, 0.5)
_EXTENSION_TARGETS_WAVE3 = (1.0, 1.618, 2.618)
_EXTENSION_TARGETS_WAVE5 = (0.618, 1.0, 1.618)

_SUBJECTIVITY_NOTE = (
    "Elliott Wave counts are inherently subjective — this is one "
    "rule-validated, Fibonacci-scored candidate, not a definitive read. "
    "Treat it as a hypothesis to check against your own chart reading."
)


def _pivot(times: np.ndarray, idx: int, price: float, kind: str, confirmed_at: float) -> dict:
    return {
        "time": int(times[idx]),
        "price": round(float(price), 6),
        "type": kind,
        "confirmedAt": int(confirmed_at),
    }


def zigzag(candles: list[dict], atr_mult: float = DEFAULT_ATR_MULT) -> list[dict]:
    """Ordered, alternating swing highs/lows, threshold = atr_mult x ATR(14).

    ATR-relative (not a fixed %) so the same multiplier is sensible across
    wildly different price scales (a $2 stock vs. $60k BTC) — the same
    reasoning as the platform's existing ATR-scaled stop/target defaults.

    Only *confirmed* pivots are returned: a running extreme is tracked but
    never emitted until price reverses past it by the threshold, so this
    function is causal by construction — a consumer walking the list bar by
    bar never sees a pivot before it actually happened. `confirmedAt` (the
    bar where the reversal crossed threshold) is tracked separately from
    `time` (the extreme's own bar) for exactly that reason.

    Known simplification: once a pivot confirms and tracking flips to the
    opposite direction, an even-more-extreme move back through the just-
    confirmed pivot before the *next* one confirms is not retroactively
    un-done. This occasionally yields a slightly non-optimal pivot; the
    wave-labeling pass downstream is robust to that (it validates whatever
    window it's given rather than assuming a perfect zigzag).
    """
    df = to_df(candles)
    if len(df) < 20:
        return []
    highs, lows, times = df["high"].values, df["low"].values, df["time"].values
    atr_vals = atr(df).values

    start = next((i for i, v in enumerate(atr_vals) if not np.isnan(v)), None)
    if start is None or start >= len(df) - 2:
        return []

    pivots: list[dict] = []
    direction: str | None = None  # 'up' = tracking for a high, 'down' = tracking for a low
    cand_high, cand_high_idx = highs[start], start
    cand_low, cand_low_idx = lows[start], start

    for i in range(start + 1, len(df)):
        h, l, thresh = highs[i], lows[i], atr_mult * atr_vals[i]
        if np.isnan(thresh):
            continue

        if direction is None:
            if h > cand_high:
                cand_high, cand_high_idx = h, i
            if l < cand_low:
                cand_low, cand_low_idx = l, i
            if cand_high - l >= thresh and cand_high_idx != i:
                pivots.append(_pivot(times, cand_high_idx, cand_high, "high", times[i]))
                direction = "down"
                cand_low, cand_low_idx = l, i
            elif h - cand_low >= thresh and cand_low_idx != i:
                pivots.append(_pivot(times, cand_low_idx, cand_low, "low", times[i]))
                direction = "up"
                cand_high, cand_high_idx = h, i

        elif direction == "down":
            if l < cand_low:
                cand_low, cand_low_idx = l, i
            if h - cand_low >= thresh and cand_low_idx != i:
                pivots.append(_pivot(times, cand_low_idx, cand_low, "low", times[i]))
                direction = "up"
                cand_high, cand_high_idx = h, i

        else:  # direction == "up"
            if h > cand_high:
                cand_high, cand_high_idx = h, i
            if cand_high - l >= thresh and cand_high_idx != i:
                pivots.append(_pivot(times, cand_high_idx, cand_high, "high", times[i]))
                direction = "down"
                cand_low, cand_low_idx = l, i

    return pivots


def _zone_score(value: float, lo: float, hi: float) -> float:
    """1.0 inside [lo, hi], decaying linearly outside it."""
    if lo <= value <= hi:
        return 1.0
    d = lo - value if value < lo else value - hi
    span = hi - lo if hi > lo else 1.0
    return max(0.0, 1.0 - d / span)


def _nearest_target_score(value: float, targets: tuple[float, ...]) -> float:
    d = min(abs(value - t) for t in targets)
    return max(0.0, 1.0 - d)


_MAX_LOOKBACK_PIVOTS = 40  # how far back to slide the search for a valid window


def _try_impulse(window: list[dict], last_price: float | None) -> dict | None:
    """Validate one candidate window: 5 pivots + a live price (in-progress
    wave 5) or 6 pivots (fully confirmed). Returns None if any hard rule
    fails."""
    live = last_price is not None and len(window) == 5
    if not live and len(window) != 6:
        return None

    p0, p1, p2, p3, p4 = window[0], window[1], window[2], window[3], window[4]
    p5 = None if live else window[5]
    direction = "bullish" if p1["price"] > p0["price"] else "bearish"
    sign = 1 if direction == "bullish" else -1

    w1 = sign * (p1["price"] - p0["price"])
    w2 = sign * (p1["price"] - p2["price"])
    w3 = sign * (p3["price"] - p2["price"])
    w4 = sign * (p3["price"] - p4["price"])
    if w1 <= 0 or w3 <= 0:
        return None
    if w2 >= w1:                                       # rule: wave 2 never retraces past wave 1's start
        return None
    if sign * (p4["price"] - p1["price"]) <= 0:          # rule: wave 4 never enters wave 1's territory
        return None

    if live:
        w5 = sign * (last_price - p4["price"])
        if w5 <= 0:
            return None
        waves = [
            {"label": "1", "time": p1["time"], "price": p1["price"], "confirmed": True},
            {"label": "2", "time": p2["time"], "price": p2["price"], "confirmed": True},
            {"label": "3", "time": p3["time"], "price": p3["price"], "confirmed": True},
            {"label": "4", "time": p4["time"], "price": p4["price"], "confirmed": True},
            {"label": "5", "time": p4["time"], "price": last_price, "confirmed": False},
        ]
        score = (
            _zone_score(w2 / w1, *_RETRACE_ZONE_WAVE2)
            + _zone_score(w4 / w3, *_RETRACE_ZONE_WAVE4)
            + _nearest_target_score(w3 / w1, _EXTENSION_TARGETS_WAVE3)
        ) / 3
        target = p4["price"] + sign * w1
        side = "low" if direction == "bullish" else "high"
        return {
            "waveType": "impulse",
            "direction": direction,
            "waves": waves,
            "confidence": round(score * 100, 1),
            "projectedTarget": round(float(target), 4),
            "projectionBasis": f"Wave 5 ≈ Wave 1 in length, projected from the wave 4 {side}",
        }

    w5 = sign * (p5["price"] - p4["price"])
    if w5 <= 0:
        return None
    if w3 < w1 and w3 < w5:                             # rule: wave 3 is never the shortest
        return None
    waves = [
        {"label": "1", "time": p1["time"], "price": p1["price"], "confirmed": True},
        {"label": "2", "time": p2["time"], "price": p2["price"], "confirmed": True},
        {"label": "3", "time": p3["time"], "price": p3["price"], "confirmed": True},
        {"label": "4", "time": p4["time"], "price": p4["price"], "confirmed": True},
        {"label": "5", "time": p5["time"], "price": p5["price"], "confirmed": True},
    ]
    score = (
        _zone_score(w2 / w1, *_RETRACE_ZONE_WAVE2)
        + _zone_score(w4 / w3, *_RETRACE_ZONE_WAVE4)
        + _nearest_target_score(w3 / w1, _EXTENSION_TARGETS_WAVE3)
        + _nearest_target_score(w5 / w1, _EXTENSION_TARGETS_WAVE5)
    ) / 4
    return {
        "waveType": "impulse",
        "direction": direction,
        "waves": waves,
        "confidence": round(score * 100, 1),
        "projectedTarget": None,
        "projectionBasis": None,
    }


def _impulse_candidate(pivots: list[dict], last_price: float | None) -> dict | None:
    """Search recent windows for a valid 5-wave impulse. An in-progress wave
    5 at the current tail is checked first (most actionable: "we're in the
    middle of something now"); failing that, slide backward through
    completed 6-pivot windows and take the most recent one that validates —
    real price data is choppy, so the single most-recent window rarely forms
    a clean textbook impulse and a market-relevant count usually sits a few
    swings back."""
    if len(pivots) < 5:
        return None

    live = _try_impulse(pivots[-5:], last_price)
    if live is not None:
        return live

    n = len(pivots)
    min_end = max(6, n - _MAX_LOOKBACK_PIVOTS)
    for end in range(n, min_end - 1, -1):
        if end < 6:
            break
        result = _try_impulse(pivots[end - 6:end], last_price=None)
        if result is not None:
            return result
    return None


def _try_correction(window: list[dict]) -> dict | None:
    p0, p1, p2, p3 = window
    direction = "bearish" if p1["price"] > p0["price"] else "bullish"  # A moves against the prior trend
    sign = -1 if direction == "bearish" else 1

    wa = sign * (p1["price"] - p0["price"])
    wb = sign * (p1["price"] - p2["price"])
    wc = sign * (p3["price"] - p2["price"])
    if wa <= 0 or wc <= 0 or wb <= 0:
        return None

    score = (
        _zone_score(wb / wa, 0.382, 0.786)
        + _nearest_target_score(wc / wa, (0.618, 1.0, 1.618))
    ) / 2
    waves = [
        {"label": "A", "time": p1["time"], "price": p1["price"], "confirmed": True},
        {"label": "B", "time": p2["time"], "price": p2["price"], "confirmed": True},
        {"label": "C", "time": p3["time"], "price": p3["price"], "confirmed": True},
    ]
    return {
        "waveType": "correction",
        "direction": direction,
        "waves": waves,
        "confidence": round(min(score * 100, 65.0), 1),  # capped — corrections are structurally ambiguous
        "projectedTarget": None,
        "projectionBasis": None,
    }


def _correction_candidate(pivots: list[dict]) -> dict | None:
    """Slide backward through 4-pivot windows for the most recent valid A-B-C.
    Kept deliberately light — corrective structures (zigzag/flat/expanded-
    flat) have different valid overlap rules, so this only checks alternation
    + basic proportion rather than a full sub-type classification."""
    if len(pivots) < 4:
        return None
    n = len(pivots)
    min_end = max(4, n - _MAX_LOOKBACK_PIVOTS)
    for end in range(n, min_end - 1, -1):
        if end < 4:
            break
        result = _try_correction(pivots[end - 4:end])
        if result is not None:
            return result
    return None


def label_waves(pivots: list[dict], last_price: float | None = None) -> dict:
    """Best-effort wave count. Search order: an in-progress or just-completed
    impulse first (most actionable), then a corrective ABC, then give up.
    See module docstring for the subjectivity caveat this always carries."""
    empty = {
        "waveType": None, "direction": None, "waves": [], "confidence": 0.0,
        "projectedTarget": None, "projectionBasis": None,
    }
    result = _impulse_candidate(pivots, last_price) or _correction_candidate(pivots) or empty
    notes = [_SUBJECTIVITY_NOTE]
    if result["waveType"] is None:
        notes.append("No valid impulse or corrective count found in the recent swing structure.")
    elif result["waveType"] == "impulse" and any(not w["confirmed"] for w in result["waves"]):
        notes.append("Wave 5 is unconfirmed and based on the live price — it can still fail or extend.")
    result["notes"] = notes
    return result


def compute_elliott_wave(candles: list[dict], atr_mult: float = DEFAULT_ATR_MULT) -> dict:
    """Bundle function, same convention as compute_indicators/compute_signals."""
    pivots = zigzag(candles, atr_mult)
    last_price = float(candles[-1]["close"]) if candles else None
    return {"pivots": pivots, **label_waves(pivots, last_price)}

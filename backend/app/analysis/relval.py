"""Relative value analytics for a pair of tickers.

Ratio chart, rolling z-score of the log-spread, rolling correlation, and a
mean-reversion half-life from an Ornstein-Uhlenbeck (AR(1)) fit on the
log-spread.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import client

PRESET_PAIRS = [
    {"a": "QQQ", "b": "SPY", "label": "Nasdaq vs S&P (growth/beta)"},
    {"a": "GLD", "b": "SLV", "label": "Gold vs Silver"},
    {"a": "XLE", "b": "CL=F", "label": "Energy equities vs Crude"},
    {"a": "HYG", "b": "TLT", "label": "High yield vs Treasuries (credit risk)"},
    {"a": "IWM", "b": "SPY", "label": "Small caps vs Large caps"},
    {"a": "XLF", "b": "^TNX", "label": "Banks vs 10Y yield"},
]


def _aligned_closes(a: str, b: str, period: str) -> pd.DataFrame:
    ha = client.get_history(a, period=period)
    hb = client.get_history(b, period=period)
    sa = pd.Series({c["time"]: c["close"] for c in ha["candles"]}, name="a")
    sb = pd.Series({c["time"]: c["close"] for c in hb["candles"]}, name="b")
    df = pd.concat([sa, sb], axis=1).dropna()
    if len(df) < 130:
        raise ValueError("not enough overlapping history for pair analysis")
    df["stale"] = ha.get("stale", False) or hb.get("stale", False)
    return df


def _half_life(spread: pd.Series) -> float | None:
    """OU half-life via AR(1): ds = a + b*s_lag + e, HL = -ln(2)/b."""
    s_lag = spread.shift(1).dropna()
    ds = spread.diff().dropna()
    s_lag, ds = s_lag.align(ds, join="inner")
    x = np.vstack([np.ones(len(s_lag)), s_lag.values]).T
    try:
        beta = np.linalg.lstsq(x, ds.values, rcond=None)[0][1]
    except np.linalg.LinAlgError:
        return None
    if beta >= 0:
        return None  # not mean-reverting over this window
    return float(-np.log(2) / beta)


def analyze_pair(a: str, b: str, period: str = "2y", window: int = 60) -> dict:
    df = _aligned_closes(a, b, period)
    times = df.index.tolist()
    ratio = df["a"] / df["b"]
    log_spread = np.log(df["a"]) - np.log(df["b"])

    roll_mean = log_spread.rolling(window).mean()
    roll_std = log_spread.rolling(window).std()
    zscore = (log_spread - roll_mean) / roll_std
    corr = df["a"].pct_change().rolling(window).corr(df["b"].pct_change())

    def series(vals: pd.Series) -> list[dict]:
        return [
            {"time": int(t), "value": round(float(v), 6)}
            for t, v in zip(times, vals)
            if not pd.isna(v)
        ]

    hl = _half_life(log_spread)
    z_last = zscore.iloc[-1]
    return {
        "a": a.upper(),
        "b": b.upper(),
        "window": window,
        "ratio": series(ratio),
        "zscore": series(zscore),
        "correlation": series(corr),
        "current": {
            "ratio": round(float(ratio.iloc[-1]), 6),
            "zscore": round(float(z_last), 3) if not pd.isna(z_last) else None,
            "correlation": round(float(corr.iloc[-1]), 3) if not pd.isna(corr.iloc[-1]) else None,
            "halfLifeDays": round(hl, 1) if hl is not None and hl < 500 else None,
            "read": (
                "stretched_rich" if not pd.isna(z_last) and z_last > 2
                else "stretched_cheap" if not pd.isna(z_last) and z_last < -2
                else "normal"
            ),
        },
        "stale": bool(df["stale"].iloc[-1]),
    }

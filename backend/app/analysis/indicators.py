"""Technical indicators computed in pandas (no TA-Lib).

All public functions take the candle list produced by data.client and return
JSON-serializable structures. Series are aligned to candle timestamps and
NaN-padded values are dropped client-side friendly (null).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_df(candles: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    return df


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / length, min_periods=length).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / length, min_periods=length).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = close.ewm(span=fast).mean() - close.ewm(span=slow).mean()
    sig = line.ewm(span=signal).mean()
    return line, sig, line - sig


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0):
    mid = close.rolling(length).mean()
    sd = close.rolling(length).std()
    return mid + mult * sd, mid, mid - mult * sd


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, min_periods=length).mean()


def session_vwap(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Session-anchored VWAP with running stdev band width (intraday bars)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].fillna(0)
    session = df["dt"].dt.tz_convert("America/New_York").dt.date
    cum_pv = (typical * vol).groupby(session).cumsum()
    cum_v = vol.groupby(session).cumsum().replace(0, np.nan)
    vwap = cum_pv / cum_v
    # volume-weighted stdev of typical price around vwap, per session
    dev2 = (typical - vwap) ** 2 * vol
    band = np.sqrt(dev2.groupby(session).cumsum() / cum_v)
    return vwap, band


def pivot_levels(df: pd.DataFrame, lookaround: int = 5, tol_pct: float = 0.75,
                 max_levels: int = 8) -> list[dict]:
    """Support/resistance via swing-pivot clustering.

    A bar is a pivot high if its high is the max of +/- `lookaround` bars
    (symmetric for lows). Nearby pivots (within tol_pct %) are clustered;
    cluster strength = touch count. Returns strongest levels first.
    """
    highs, lows = df["high"].values, df["low"].values
    n = len(df)
    pivots: list[float] = []
    for i in range(lookaround, n - lookaround):
        win_h = highs[i - lookaround : i + lookaround + 1]
        win_l = lows[i - lookaround : i + lookaround + 1]
        if highs[i] == win_h.max():
            pivots.append(highs[i])
        if lows[i] == win_l.min():
            pivots.append(lows[i])
    if not pivots:
        return []
    pivots.sort()
    clusters: list[list[float]] = [[pivots[0]]]
    for p in pivots[1:]:
        if abs(p - clusters[-1][-1]) / p * 100 <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    last_close = float(df["close"].iloc[-1])
    levels = [
        {
            "price": round(float(np.mean(c)), 4),
            "touches": len(c),
            "kind": "resistance" if np.mean(c) > last_close else "support",
        }
        for c in clusters
        if len(c) >= 2
    ]
    levels.sort(key=lambda x: -x["touches"])
    return levels[:max_levels]


def _series(times: pd.Series, values: pd.Series) -> list[dict]:
    out = []
    for t, v in zip(times, values):
        if pd.isna(v):
            continue
        out.append({"time": int(t), "value": round(float(v), 6)})
    return out


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["close"].diff()).fillna(0)
    return (direction * df["volume"]).cumsum()


def stochastic(df: pd.DataFrame, length: int = 14,
               smooth_k: int = 3, smooth_d: int = 3):
    """Stochastic oscillator — returns (smoothed %K, %D)."""
    low_min = df["low"].rolling(length).min()
    high_max = df["high"].rolling(length).max()
    rng = (high_max - low_min).replace(0, np.nan)
    k = 100 * (df["close"] - low_min) / rng
    k_smooth = k.rolling(smooth_k).mean()
    return k_smooth, k_smooth.rolling(smooth_d).mean()


def cci(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Commodity Channel Index."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    mean = typical.rolling(length).mean()
    mad = typical.rolling(length).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (typical - mean) / (0.015 * mad.replace(0, np.nan))


def keltner(df: pd.DataFrame, length: int = 20, mult: float = 2.0):
    """Keltner channel — returns (upper, mid, lower). Mid is EMA; bands are ±mult×ATR."""
    mid = df["close"].ewm(span=length, adjust=False).mean()
    atr_val = atr(df, length)
    return mid + mult * atr_val, mid, mid - mult * atr_val


def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average Directional Index — trend strength, not direction."""
    plus_dm = df["high"].diff().clip(lower=0)
    minus_dm = (-df["low"].diff()).clip(lower=0)
    plus_dm = plus_dm.where(plus_dm >= minus_dm, 0.0)
    minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)
    atr_val = atr(df, length)
    plus_di = 100 * plus_dm.ewm(alpha=1 / length, min_periods=length).mean() / atr_val
    minus_di = 100 * minus_dm.ewm(alpha=1 / length, min_periods=length).mean() / atr_val
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / length, min_periods=length).mean()


def compute_indicators(candles: list[dict]) -> dict:
    df = to_df(candles)
    close, times = df["close"], df["time"]
    macd_line, macd_sig, macd_hist = macd(close)
    bb_u, bb_m, bb_l = bollinger(close)
    rsi14 = rsi(close)
    rsi_z = (rsi14 - rsi14.rolling(252, min_periods=60).mean()) / rsi14.rolling(
        252, min_periods=60
    ).std()
    return {
        "sma20": _series(times, close.rolling(20).mean()),
        "sma50": _series(times, close.rolling(50).mean()),
        "sma200": _series(times, close.rolling(200).mean()),
        "ema9": _series(times, close.ewm(span=9).mean()),
        "ema21": _series(times, close.ewm(span=21).mean()),
        "rsi": _series(times, rsi14),
        "rsiZ": _series(times, rsi_z),
        "macd": _series(times, macd_line),
        "macdSignal": _series(times, macd_sig),
        "macdHist": _series(times, macd_hist),
        "bbUpper": _series(times, bb_u),
        "bbLower": _series(times, bb_l),
        "atr": _series(times, atr(df)),
        "obv": _series(times, obv(df)),
        "levels": pivot_levels(df),
    }


def compute_vwap(candles: list[dict]) -> dict:
    df = to_df(candles)
    vwap, band = session_vwap(df)
    times = df["time"]
    return {
        "vwap": _series(times, vwap),
        "upper1": _series(times, vwap + band),
        "lower1": _series(times, vwap - band),
        "upper2": _series(times, vwap + 2 * band),
        "lower2": _series(times, vwap - 2 * band),
    }


def compute_signals(candles: list[dict]) -> dict:
    """Distilled per-ticker read: trend, momentum, mean-reversion stretch."""
    df = to_df(candles)
    close = df["close"]
    last = float(close.iloc[-1])
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    rsi14 = rsi(close)
    macd_line, macd_sig, _ = macd(close)
    bb_u, bb_m, bb_l = bollinger(close)
    atr14 = atr(df)

    def val(s: pd.Series) -> float | None:
        v = s.iloc[-1]
        return None if pd.isna(v) else round(float(v), 4)

    trend = "neutral"
    if val(sma50) and val(sma200):
        above50, above200 = last > sma50.iloc[-1], last > sma200.iloc[-1]
        golden = sma50.iloc[-1] > sma200.iloc[-1]
        if above50 and above200 and golden:
            trend = "uptrend"
        elif not above50 and not above200 and not golden:
            trend = "downtrend"

    # golden/death cross within the last 10 bars
    cross = None
    if len(sma200.dropna()) > 10:
        diff = (sma50 - sma200).iloc[-11:]
        signs = np.sign(diff.values)
        if signs[0] < 0 and signs[-1] > 0:
            cross = "golden_cross"
        elif signs[0] > 0 and signs[-1] < 0:
            cross = "death_cross"

    r = val(rsi14)
    momentum = "neutral"
    if r is not None:
        momentum = "overbought" if r >= 70 else "oversold" if r <= 30 else "neutral"

    stretch = None
    if val(bb_u) is not None:
        width = bb_u.iloc[-1] - bb_l.iloc[-1]
        if width > 0:
            stretch = round(float((last - bb_m.iloc[-1]) / (width / 2)), 3)

    return {
        "last": round(last, 4),
        "trend": trend,
        "recentCross": cross,
        "rsi": r,
        "momentum": momentum,
        "macdAboveSignal": bool(macd_line.iloc[-1] > macd_sig.iloc[-1])
        if not pd.isna(macd_line.iloc[-1])
        else None,
        "bollingerStretch": stretch,  # -1..+1 within bands; beyond = outside bands
        "atrPct": round(float(atr14.iloc[-1] / last * 100), 3)
        if not pd.isna(atr14.iloc[-1])
        else None,
        "sma50": val(sma50),
        "sma200": val(sma200),
        "levels": pivot_levels(df)[:5],
    }

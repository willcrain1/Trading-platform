"""Vectorized long/flat backtests on daily bars.

Signals are computed on close and executed on the NEXT bar's close (no
look-ahead). Strategies return a 0/1 position series; the engine turns that
into an equity curve and summary stats vs buy-and-hold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data import client
from . import indicators as ind

TRADING_DAYS = 252


# ── original 4 ───────────────────────────────────────────────────────────────

def _strat_rsi_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    r = ind.rsi(df["close"], p.get("length", 14))
    pos = pd.Series(np.nan, index=df.index)
    pos[r < p.get("buyBelow", 30)] = 1.0
    pos[r > p.get("exitAbove", 50)] = 0.0
    return pos.ffill().fillna(0)


def _strat_sma_cross(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].rolling(p.get("fast", 50)).mean()
    slow = df["close"].rolling(p.get("slow", 200)).mean()
    return (fast > slow).astype(float)


def _strat_macd(df: pd.DataFrame, p: dict) -> pd.Series:
    line, sig, _ = ind.macd(df["close"], p.get("fast", 12), p.get("slow", 26), p.get("signal", 9))
    return (line > sig).astype(float)


def _strat_bollinger_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    upper, mid, lower = ind.bollinger(df["close"], p.get("length", 20), p.get("mult", 2.0))
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] < lower] = 1.0
    pos[df["close"] > mid] = 0.0
    return pos.ffill().fillna(0)


# ── EMA crosses ───────────────────────────────────────────────────────────────

def _strat_ema_cross_9_21(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].ewm(span=p.get("fast", 9), adjust=False).mean()
    slow = df["close"].ewm(span=p.get("slow", 21), adjust=False).mean()
    return (fast > slow).astype(float)


def _strat_ema_cross_21_50(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].ewm(span=p.get("fast", 21), adjust=False).mean()
    slow = df["close"].ewm(span=p.get("slow", 50), adjust=False).mean()
    return (fast > slow).astype(float)


# ── Donchian channel breakout ─────────────────────────────────────────────────

def _strat_donchian_breakout(df: pd.DataFrame, p: dict) -> pd.Series:
    n = p.get("length", 20)
    # use yesterday's N-day high/low to avoid look-ahead
    upper = df["close"].rolling(n).max().shift(1)
    lower = df["close"].rolling(n).min().shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] > upper] = 1.0
    pos[df["close"] < lower] = 0.0
    return pos.ffill().fillna(0)


# ── Dual momentum (abs + relative vs SPY) ────────────────────────────────────

def _strat_dual_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    lookback = p.get("lookback", 252)
    abs_mom = df["close"] > df["close"].shift(lookback)
    try:
        spy = client.get_history("SPY", period="2y")
        spy_df = ind.to_df(spy["candles"])[["time", "close"]].rename(columns={"close": "spy"})
        merged = pd.merge_asof(df[["time"]], spy_df, on="time", direction="nearest")
        spy_close = pd.Series(merged["spy"].values, index=df.index)
        ticker_ret = df["close"] / df["close"].shift(lookback)
        spy_ret = spy_close / spy_close.shift(lookback)
        rel_mom = ticker_ret > spy_ret
        return (abs_mom & rel_mom).astype(float)
    except Exception:
        return abs_mom.astype(float)


# ── Stochastic mean reversion ─────────────────────────────────────────────────

def _strat_stochastic(df: pd.DataFrame, p: dict) -> pd.Series:
    k, _ = ind.stochastic(df, p.get("length", 14), p.get("smoothK", 3), p.get("smoothD", 3))
    pos = pd.Series(np.nan, index=df.index)
    pos[k < p.get("buyBelow", 20)] = 1.0
    pos[k > p.get("sellAbove", 80)] = 0.0
    return pos.ffill().fillna(0)


# ── CCI mean reversion ────────────────────────────────────────────────────────

def _strat_cci_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    c = ind.cci(df, p.get("length", 20))
    pos = pd.Series(np.nan, index=df.index)
    pos[c < p.get("buyBelow", -100)] = 1.0
    pos[c > p.get("exitAbove", 0)] = 0.0
    return pos.ffill().fillna(0)


# ── Keltner channel mean reversion ────────────────────────────────────────────

def _strat_keltner_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    upper, mid, lower = ind.keltner(df, p.get("length", 20), p.get("mult", 2.0))
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] < lower] = 1.0
    pos[df["close"] > mid] = 0.0
    return pos.ffill().fillna(0)


# ── Supertrend ────────────────────────────────────────────────────────────────

def _strat_supertrend(df: pd.DataFrame, p: dict) -> pd.Series:
    length = p.get("length", 10)
    mult = p.get("mult", 3.0)
    atr_val = ind.atr(df, length)
    hl2 = (df["high"] + df["low"]) / 2
    ub_basic = (hl2 + mult * atr_val).values
    lb_basic = (hl2 - mult * atr_val).values
    n = len(df)
    ub = ub_basic.copy()
    lb = lb_basic.copy()
    trend = np.ones(n)
    close = df["close"].values

    for i in range(1, n):
        if np.isnan(ub_basic[i]) or np.isnan(lb_basic[i]):
            ub[i] = ub[i - 1]
            lb[i] = lb[i - 1]
            trend[i] = trend[i - 1]
            continue
        ub[i] = ub_basic[i] if (ub_basic[i] < ub[i - 1] or close[i - 1] > ub[i - 1]) else ub[i - 1]
        lb[i] = lb_basic[i] if (lb_basic[i] > lb[i - 1] or close[i - 1] < lb[i - 1]) else lb[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if close[i] < lb[i] else 1
        else:
            trend[i] = 1 if close[i] > ub[i] else -1

    return pd.Series((trend > 0).astype(float), index=df.index)


# ── Triple SMA alignment ──────────────────────────────────────────────────────

def _strat_triple_sma(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].rolling(p.get("fast", 10)).mean()
    mid = df["close"].rolling(p.get("mid", 30)).mean()
    slow = df["close"].rolling(p.get("slow", 100)).mean()
    return ((fast > mid) & (mid > slow)).astype(float)


# ── Parabolic SAR ─────────────────────────────────────────────────────────────

def _strat_parabolic_sar(df: pd.DataFrame, p: dict) -> pd.Series:
    af_step = p.get("afStep", 0.02)
    af_max = p.get("afMax", 0.2)
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    n = len(df)
    sar = np.zeros(n)
    ep = high[0]
    af = af_step
    bull = True
    sar[0] = low[0]

    for i in range(1, n):
        if bull:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            sar[i] = min(sar[i], low[i - 1], low[max(0, i - 2)])
            if high[i] > ep:
                ep = high[i]
                af = min(af + af_step, af_max)
            if low[i] < sar[i]:
                bull = False
                sar[i] = ep
                ep = low[i]
                af = af_step
        else:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            sar[i] = max(sar[i], high[i - 1], high[max(0, i - 2)])
            if low[i] < ep:
                ep = low[i]
                af = min(af + af_step, af_max)
            if high[i] > sar[i]:
                bull = True
                sar[i] = ep
                ep = high[i]
                af = af_step

    return pd.Series((close > sar).astype(float), index=df.index)


# ── Rate-of-change momentum ───────────────────────────────────────────────────

def _strat_roc_momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    length = p.get("length", 125)  # ~6 months default
    entry_pct = p.get("entryPct", 0.0)
    exit_pct = p.get("exitPct", -5.0)
    roc = df["close"].pct_change(length) * 100
    pos = pd.Series(np.nan, index=df.index)
    pos[roc > entry_pct] = 1.0
    pos[roc < exit_pct] = 0.0
    return pos.ffill().fillna(0)


# ── Z-score mean reversion ────────────────────────────────────────────────────

def _strat_zscore_revert(df: pd.DataFrame, p: dict) -> pd.Series:
    length = p.get("length", 20)
    entry_z = p.get("entryZ", -2.0)
    exit_z = p.get("exitZ", 0.0)
    close = df["close"]
    mean = close.rolling(length).mean()
    std = close.rolling(length).std().replace(0, np.nan)
    z = (close - mean) / std
    pos = pd.Series(np.nan, index=df.index)
    pos[z < entry_z] = 1.0
    pos[z > exit_z] = 0.0
    return pos.ffill().fillna(0)


# ── Turtle trading ────────────────────────────────────────────────────────────

def _strat_turtle(df: pd.DataFrame, p: dict) -> pd.Series:
    entry_n = p.get("entryN", 20)
    exit_n = p.get("exitN", 10)
    # shift(1): use yesterday's high/low to avoid look-ahead
    entry_high = df["close"].rolling(entry_n).max().shift(1)
    exit_low = df["close"].rolling(exit_n).min().shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["close"] > entry_high] = 1.0
    pos[df["close"] < exit_low] = 0.0
    return pos.ffill().fillna(0)


# ── Long / short (LS) trend variants ─────────────────────────────────────────
# Return +1 (long), 0 (flat during warmup), or −1 (short).
# The backtest math `rets * pos` correctly handles −1 as a short.

def _strat_sma_cross_ls(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].rolling(p.get("fast", 50)).mean()
    slow = df["close"].rolling(p.get("slow", 200)).mean()
    valid = fast.notna() & slow.notna()
    return ((fast > slow).astype(float) * 2 - 1).where(valid, 0.0)


def _strat_supertrend_ls(df: pd.DataFrame, p: dict) -> pd.Series:
    """Supertrend long/short: +1 when bullish, −1 when bearish."""
    long_signal = _strat_supertrend(df, p)  # 1 or 0
    return long_signal * 2 - 1  # 1 or -1 (warmup NaN → -1, but ATR warmup keeps it at 0→-1)


def _strat_ema_cross_21_50_ls(df: pd.DataFrame, p: dict) -> pd.Series:
    fast = df["close"].ewm(span=p.get("fast", 21), adjust=False).mean()
    slow = df["close"].ewm(span=p.get("slow", 50), adjust=False).mean()
    return (fast > slow).astype(float) * 2 - 1


# ── VIX regime filter ─────────────────────────────────────────────────────────
# Mean-reversion strategies gate entries on VIX < threshold: when volatility is
# elevated, dips can keep falling rather than reverting.

def _vix_series(df: pd.DataFrame) -> pd.Series | None:
    """Fetch ^VIX and align to df timestamps. Returns None on failure."""
    try:
        vix = client.get_history("^VIX", period="2y")
        vix_df = ind.to_df(vix["candles"])[["time", "close"]].rename(columns={"close": "vix"})
        merged = pd.merge_asof(df[["time"]], vix_df, on="time", direction="nearest")
        return pd.Series(merged["vix"].values, index=df.index)
    except Exception:
        return None


def _strat_rsi_revert_vix(df: pd.DataFrame, p: dict) -> pd.Series:
    """RSI mean reversion, only active when VIX is below threshold (default 25)."""
    base = _strat_rsi_revert(df, p)
    vix = _vix_series(df)
    if vix is None:
        return base
    low_vol = vix < p.get("maxVix", 25)
    return base.where(low_vol, 0.0)


def _strat_bollinger_vix(df: pd.DataFrame, p: dict) -> pd.Series:
    """Bollinger band revert, only active when VIX < threshold."""
    base = _strat_bollinger_revert(df, p)
    vix = _vix_series(df)
    if vix is None:
        return base
    low_vol = vix < p.get("maxVix", 25)
    return base.where(low_vol, 0.0)


# ── Bond rotation ─────────────────────────────────────────────────────────────
# When the equity signal is off (flat), rotate into TLT rather than sitting in
# 0%-return paper cash. Strategies marked with bondTicker get blended returns
# in run_backtest. For live trading: set up a separate TLT instance.

def _strat_sma_bond_rotate(df: pd.DataFrame, p: dict) -> pd.Series:
    """SMA cross signal: 1=equity, 0=bonds (handled by bondTicker in runner)."""
    fast = df["close"].rolling(p.get("fast", 50)).mean()
    slow = df["close"].rolling(p.get("slow", 200)).mean()
    both_valid = fast.notna() & slow.notna()
    return (fast > slow).astype(float).where(both_valid, np.nan).ffill().fillna(0)


def _strat_dual_momentum_bond(df: pd.DataFrame, p: dict) -> pd.Series:
    """Dual momentum: 1=equity, 0=bonds. Falls back to abs-only if SPY fails."""
    lookback = p.get("lookback", 252)
    abs_mom = df["close"] > df["close"].shift(lookback)
    try:
        spy = client.get_history("SPY", period="2y")
        spy_df = ind.to_df(spy["candles"])[["time", "close"]].rename(columns={"close": "spy"})
        merged = pd.merge_asof(df[["time"]], spy_df, on="time", direction="nearest")
        spy_close = pd.Series(merged["spy"].values, index=df.index)
        rel_mom = (df["close"] / df["close"].shift(lookback)) > (spy_close / spy_close.shift(lookback))
        return (abs_mom & rel_mom).astype(float)
    except Exception:
        return abs_mom.astype(float)


# ── Strategy registry ─────────────────────────────────────────────────────────
# maxHoldDays: default time-stop for trade plans (trading days).
# Trend strategies get room to run; mean-reversion trades should resolve quickly.

STRATEGIES = {
    # ── original ──────────────────────────────────────────────────────────────
    "rsi_revert": {
        "fn": _strat_rsi_revert,
        "label": "RSI mean reversion",
        "params": {"length": 14, "buyBelow": 30, "exitAbove": 50},
        "maxHoldDays": 20,
    },
    "sma_cross": {
        "fn": _strat_sma_cross,
        "label": "SMA cross (50/200)",
        "params": {"fast": 50, "slow": 200},
        "maxHoldDays": 90,
    },
    "macd_cross": {
        "fn": _strat_macd,
        "label": "MACD cross",
        "params": {"fast": 12, "slow": 26, "signal": 9},
        "maxHoldDays": 60,
    },
    "bollinger_revert": {
        "fn": _strat_bollinger_revert,
        "label": "Bollinger band revert",
        "params": {"length": 20, "mult": 2.0},
        "maxHoldDays": 20,
    },
    # ── EMA crosses ───────────────────────────────────────────────────────────
    "ema_cross_9_21": {
        "fn": _strat_ema_cross_9_21,
        "label": "EMA cross (9/21)",
        "params": {"fast": 9, "slow": 21},
        "maxHoldDays": 30,
    },
    "ema_cross_21_50": {
        "fn": _strat_ema_cross_21_50,
        "label": "EMA cross (21/50)",
        "params": {"fast": 21, "slow": 50},
        "maxHoldDays": 60,
    },
    # ── breakout / momentum ───────────────────────────────────────────────────
    "donchian_breakout": {
        "fn": _strat_donchian_breakout,
        "label": "Donchian breakout (20-day)",
        "params": {"length": 20},
        "maxHoldDays": 60,
    },
    "dual_momentum": {
        "fn": _strat_dual_momentum,
        "label": "Dual momentum (abs + rel vs SPY)",
        "params": {"lookback": 252},
        "maxHoldDays": 90,
    },
    "roc_momentum": {
        "fn": _strat_roc_momentum,
        "label": "Rate-of-change momentum (6-mo)",
        "params": {"length": 125, "entryPct": 0.0, "exitPct": -5.0},
        "maxHoldDays": 90,
    },
    "turtle": {
        "fn": _strat_turtle,
        "label": "Turtle (20-day breakout / 10-day exit)",
        "params": {"entryN": 20, "exitN": 10},
        "maxHoldDays": 60,
    },
    # ── oscillator mean-reversion ─────────────────────────────────────────────
    "stochastic": {
        "fn": _strat_stochastic,
        "label": "Stochastic mean reversion",
        "params": {"length": 14, "smoothK": 3, "smoothD": 3, "buyBelow": 20, "sellAbove": 80},
        "maxHoldDays": 20,
    },
    "cci_revert": {
        "fn": _strat_cci_revert,
        "label": "CCI mean reversion",
        "params": {"length": 20, "buyBelow": -100, "exitAbove": 0},
        "maxHoldDays": 20,
    },
    "zscore_revert": {
        "fn": _strat_zscore_revert,
        "label": "Z-score mean reversion",
        "params": {"length": 20, "entryZ": -2.0, "exitZ": 0.0},
        "maxHoldDays": 20,
    },
    "keltner_revert": {
        "fn": _strat_keltner_revert,
        "label": "Keltner channel revert",
        "params": {"length": 20, "mult": 2.0},
        "maxHoldDays": 20,
    },
    # ── volatility / trend systems ────────────────────────────────────────────
    "supertrend": {
        "fn": _strat_supertrend,
        "label": "Supertrend (ATR-based)",
        "params": {"length": 10, "mult": 3.0},
        "maxHoldDays": 60,
    },
    "triple_sma": {
        "fn": _strat_triple_sma,
        "label": "Triple SMA alignment (10/30/100)",
        "params": {"fast": 10, "mid": 30, "slow": 100},
        "maxHoldDays": 60,
    },
    "parabolic_sar": {
        "fn": _strat_parabolic_sar,
        "label": "Parabolic SAR",
        "params": {"afStep": 0.02, "afMax": 0.2},
        "maxHoldDays": 45,
    },
    # ── long / short variants ─────────────────────────────────────────────────
    "sma_cross_ls": {
        "fn": _strat_sma_cross_ls,
        "label": "SMA cross L/S (50/200)",
        "params": {"fast": 50, "slow": 200},
        "maxHoldDays": 90,
        "supportsShort": True,
    },
    "supertrend_ls": {
        "fn": _strat_supertrend_ls,
        "label": "Supertrend L/S",
        "params": {"length": 10, "mult": 3.0},
        "maxHoldDays": 60,
        "supportsShort": True,
    },
    "ema_cross_21_50_ls": {
        "fn": _strat_ema_cross_21_50_ls,
        "label": "EMA cross L/S (21/50)",
        "params": {"fast": 21, "slow": 50},
        "maxHoldDays": 60,
        "supportsShort": True,
    },
    # ── VIX regime-filtered ───────────────────────────────────────────────────
    "rsi_revert_vix": {
        "fn": _strat_rsi_revert_vix,
        "label": "RSI revert (VIX-filtered)",
        "params": {"length": 14, "buyBelow": 30, "exitAbove": 50, "maxVix": 25},
        "maxHoldDays": 20,
    },
    "bollinger_vix": {
        "fn": _strat_bollinger_vix,
        "label": "Bollinger revert (VIX-filtered)",
        "params": {"length": 20, "mult": 2.0, "maxVix": 25},
        "maxHoldDays": 20,
    },
    # ── bond rotation ─────────────────────────────────────────────────────────
    "sma_bond_rotate": {
        "fn": _strat_sma_bond_rotate,
        "label": "SMA cross + TLT rotation",
        "params": {"fast": 50, "slow": 200},
        "maxHoldDays": 90,
        "bondTicker": "TLT",
    },
    "dual_momentum_bond": {
        "fn": _strat_dual_momentum_bond,
        "label": "Dual momentum + TLT rotation",
        "params": {"lookback": 252},
        "maxHoldDays": 90,
        "bondTicker": "TLT",
    },
}


# ── backtest runner ───────────────────────────────────────────────────────────

def _max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def run_backtest(symbol: str, strategy: str, params: dict | None = None,
                 period: str = "5y") -> dict:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy '{strategy}'")
    spec = STRATEGIES[strategy]
    params = {**spec["params"], **(params or {})}

    hist = client.get_history(symbol, period=period)
    df = ind.to_df(hist["candles"])
    if len(df) < 60:
        raise ValueError("not enough history to backtest")

    pos = spec["fn"](df, params).shift(1).fillna(0)  # act on next bar
    rets = df["close"].pct_change().fillna(0)

    # Bond rotation: when pos=0 (flat equity), use the bond ticker's returns
    bond_ticker = spec.get("bondTicker")
    if bond_ticker:
        try:
            bond_hist = client.get_history(bond_ticker, period=period)
            bond_df = ind.to_df(bond_hist["candles"])[["time", "close"]].rename(
                columns={"close": "bond"}
            )
            merged = pd.merge_asof(df[["time"]], bond_df, on="time", direction="nearest")
            bond_close = pd.Series(merged["bond"].values, index=df.index)
            bond_rets = bond_close.pct_change().fillna(0)
            flat_mask = (pos == 0).astype(float)
            strat_rets = rets * pos + bond_rets * flat_mask
        except Exception:
            strat_rets = rets * pos  # graceful fallback to cash when flat
    else:
        strat_rets = rets * pos
    equity = (1 + strat_rets).cumprod()
    bh_equity = (1 + rets).cumprod()

    years = len(df) / TRADING_DAYS
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 else 0.0
    vol = strat_rets.std() * np.sqrt(TRADING_DAYS)
    sharpe = float(strat_rets.mean() * TRADING_DAYS / vol) if vol > 0 else None

    changes = pos.diff().fillna(pos)
    entries = df.index[changes > 0].tolist()
    exits = df.index[changes < 0].tolist()
    trades = []
    for i, e in enumerate(entries):
        x = exits[i] if i < len(exits) else df.index[-1]
        trades.append(float(df["close"].loc[x] / df["close"].loc[e] - 1))
    wins = sum(1 for t in trades if t > 0)

    def curve(s: pd.Series) -> list[dict]:
        return [{"time": int(t), "value": round(float(v), 6)}
                for t, v in zip(df["time"], s)]

    return {
        "symbol": symbol.upper(),
        "strategy": strategy,
        "label": spec["label"],
        "params": params,
        "period": period,
        "metrics": {
            "totalReturnPct": round(float(equity.iloc[-1] - 1) * 100, 2),
            "buyHoldReturnPct": round(float(bh_equity.iloc[-1] - 1) * 100, 2),
            "cagrPct": round(cagr * 100, 2),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "maxDrawdownPct": round(_max_drawdown(equity) * 100, 2),
            "buyHoldMaxDrawdownPct": round(_max_drawdown(bh_equity) * 100, 2),
            "trades": len(trades),
            "winRatePct": round(wins / len(trades) * 100, 1) if trades else None,
            "timeInMarketPct": round(float(pos.mean()) * 100, 1),
        },
        "equity": curve(equity),
        "buyHold": curve(bh_equity),
        "stale": hist.get("stale", False),
    }

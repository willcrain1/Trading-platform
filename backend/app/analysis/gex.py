"""Dealer gamma exposure (GEX) from yfinance option chains.

Standard retail-side convention: dealers are assumed short puts / long calls,
so per-strike GEX = BS-gamma * OI * 100 * spot, positive for calls and
negative for puts. Where cumulative net GEX crosses zero is the "flip point";
large |GEX| strikes act as dealer-informed support/resistance.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..data import client


def _bs_gamma(spot: float, strike: float, t_years: float, iv: float, r: float = 0.04) -> float:
    if t_years <= 0 or iv is None or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (r + iv * iv / 2) * t_years) / (iv * math.sqrt(t_years))
    pdf = math.exp(-d1 * d1 / 2) / math.sqrt(2 * math.pi)
    return pdf / (spot * iv * math.sqrt(t_years))


def _years_to(expiration: str) -> float:
    # options expire ~4pm ET; treating as end-of-day UTC is close enough here
    exp = datetime.strptime(expiration, "%Y-%m-%d").replace(hour=21, tzinfo=timezone.utc)
    return max((exp - datetime.now(timezone.utc)).total_seconds(), 0) / (365 * 24 * 3600)


def compute_gex(symbol: str, expiration: str | None = None) -> dict:
    exps = client.get_option_expirations(symbol)["expirations"]
    if not exps:
        raise client.TickerNotFound(f"{symbol} has no listed options")
    if expiration is None or expiration not in exps:
        expiration = exps[0]

    spot = client.get_quote(symbol)["last"]
    chain = client.get_option_chain(symbol, expiration)
    t = _years_to(expiration)

    by_strike: dict[float, dict] = {}
    tot_call_oi = tot_put_oi = 0.0
    for side, sign in (("calls", +1), ("puts", -1)):
        for opt in chain[side]:
            strike, oi, iv = opt.get("strike"), opt.get("openInterest") or 0, opt.get("impliedVolatility")
            if not strike or not oi:
                continue
            gex = sign * _bs_gamma(spot, strike, t, iv) * oi * 100 * spot
            slot = by_strike.setdefault(strike, {"strike": strike, "callGex": 0.0, "putGex": 0.0,
                                                 "callOi": 0, "putOi": 0})
            if sign > 0:
                slot["callGex"] += gex
                slot["callOi"] += oi
                tot_call_oi += oi
            else:
                slot["putGex"] += gex
                slot["putOi"] += oi
                tot_put_oi += oi

    strikes = sorted(by_strike.values(), key=lambda s: s["strike"])
    for s in strikes:
        s["netGex"] = s["callGex"] + s["putGex"]

    # zero-gamma flip: strike where cumulative net GEX (low -> high) crosses 0
    flip = None
    cum = 0.0
    for s in strikes:
        prev = cum
        cum += s["netGex"]
        if prev < 0 <= cum:
            flip = s["strike"]

    # max pain: strike minimizing total intrinsic payout to option holders
    max_pain, best_cost = None, None
    strike_list = [s["strike"] for s in strikes]
    for k in strike_list:
        cost = sum(s["callOi"] * max(k - s["strike"], 0) + s["putOi"] * max(s["strike"] - k, 0)
                   for s in strikes)
        if best_cost is None or cost < best_cost:
            best_cost, max_pain = cost, k

    net_total = sum(s["netGex"] for s in strikes)
    top = sorted(strikes, key=lambda s: -abs(s["netGex"]))[:6]
    return {
        "symbol": symbol.upper(),
        "spot": spot,
        "expiration": expiration,
        "expirations": exps[:12],
        "strikes": [
            {k: (round(v, 1) if isinstance(v, float) else v) for k, v in s.items()}
            for s in strikes
        ],
        "netGexTotal": round(net_total, 1),
        "flipPoint": flip,
        "maxPain": max_pain,
        "putCallOiRatio": round(tot_put_oi / tot_call_oi, 3) if tot_call_oi else None,
        "keyLevels": sorted([s["strike"] for s in top]),
        "stale": chain.get("stale", False),
    }

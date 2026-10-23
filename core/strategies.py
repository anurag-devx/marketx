"""Strategy library: the ONE definition of every strategy, used by both the backtest and the live
technical agent. Numbering follows the spec. Signal on day t close; entry at day t+1 open.

stop:   ("atr", k)        -> entry - k * ATR(t)
        ("level", col)    -> value of col at t (e.g. box_low)
target: ("atr", k)        -> entry + k * ATR(t)
        ("add", col, k)   -> entry + k * col(t)
        None              -> no fixed target; exits handle it
exits (checked at each close, after stop/target):
        "revert_sma5"     -> close > 5-day average
        "trail_sma10"     -> close < 10-day average
        "flag_off:<col>"  -> col is no longer True
"""
import numpy as np
from .config import MIN_TURNOVER_CR, MIN_PRICE


def base(d):
    return (d.turnover_cr >= MIN_TURNOVER_CR) & (d.close >= MIN_PRICE) & (d.atr > 0)


def _deliv(d):
    if "delivery_pct" in d and "deliv20" in d:
        return d.delivery_pct > d.deliv20
    return True   # no delivery data yet: filter skipped (noted in reports)


def clip(x):
    return np.clip(x, 0, 1)


STRATEGIES = {
    "S1": dict(
        name="Volume breakout",
        signal=lambda d: base(d) & (d.close > d.sma200) & (d.close > d.hh55_prev) & (d.vol_ratio >= 2),
        stop=("atr", 2.0), target=("atr", 4.0), exits=[], max_days=10,
        strength=lambda d: 0.5 * clip(d.vol_ratio / 4) + 0.5 * clip((d.close / d.hh55_prev - 1) / 0.05)),
    "S2": dict(
        name="Relative-strength leader",
        signal=lambda d: base(d) & (d.rs_pct >= 0.9) & (d.close > d.hc20_prev) & (d.close > d.sma50),
        stop=("atr", 2.0), target=("atr", 4.0), exits=[], max_days=10,
        strength=lambda d: d.rs_pct),
    "S3": dict(
        name="Tight-range breakout",
        signal=lambda d: base(d) & d.squeeze_prev & (d.close > d.box_high) & (d.vol_ratio >= 1.5) & (d.close > d.sma50),
        stop=("level", "box_low"), target=("add", "box_h", 3.0), exits=[], max_days=7,
        strength=lambda d: clip(d.vol_ratio / 3)),
    "S4": dict(
        name="52-week high momentum",
        signal=lambda d: base(d) & (d.close > d.hh252_prev) & _deliv(d),
        stop=("atr", 2.5), target=None, exits=["trail_sma10"], max_days=10,
        strength=lambda d: clip(d.vol_ratio / 3)),
    "S5": dict(
        name="Oversold dip",
        signal=lambda d: base(d) & (d.close > d.sma200) & (d.sma50 > d.sma200) & (d.rsi3 < 15),
        stop=("atr", 2.0), target=None, exits=["revert_sma5"], max_days=5,
        strength=lambda d: clip((15 - d.rsi3) / 15)),
    "S6": dict(
        name="Pullback to 20-day average",
        signal=lambda d: base(d) & (d.close > d.sma200) & (d.sma50 > d.sma200) & (d.sector_rs_pct >= 0.7)
                         & (d.low <= d.sma20) & (d.close > d.sma20) & (d.close > d.prev_close),
        stop=("level", "pb_low"), target=("atr", 3.0), exits=[], max_days=10,
        strength=lambda d: d.sector_rs_pct),
    "S10": dict(
        name="Sector rotation",
        signal=lambda d: base(d) & (d.sector_rank <= 3) & (d.rank_in_sector <= 2) & (d.close > d.sma50),
        stop=("atr", 2.0), target=None, exits=["flag_off:sector_top5"], max_days=10,
        strength=lambda d: d.rs_pct),
    # --- built for falling / volatile markets (added Oct 2026; rules fixed before any backtest was run) ---
    "S11": dict(
        name="Capitulation bounce",
        signal=lambda d: base(d) & (d.turnover_cr >= 25) & (d.rsi3 < 10)
                         & ((d.close - d.low) >= 0.5 * (d.high - d.low)) & (d.high > d.low),
        stop=("atr", 2.5), target=None, exits=["revert_sma5"], max_days=5,
        strength=lambda d: clip((10 - d.rsi3) / 10)),
    "S12": dict(
        name="Defensive leader dip",
        signal=lambda d: base(d) & (d.rs_pct >= 0.8) & (d.close > d.sma200) & (d.rsi3 < 20)
                         & ((d.atr / d.close).groupby(d.date).rank(pct=True) <= 0.4),
        stop=("atr", 2.0), target=None, exits=["revert_sma5"], max_days=7,
        strength=lambda d: d.rs_pct),
}


def signals(d, ids=None):
    """Adds sig_<id> (bool) and str_<id> (0-1) columns for the requested strategies."""
    for sid in (ids or STRATEGIES):
        s = STRATEGIES[sid]
        d[f"sig_{sid}"] = s["signal"](d).fillna(False).astype(bool)
        d[f"str_{sid}"] = s["strength"](d).astype(float).fillna(0)
    return d


def levels(sid, entry, row):
    """Stop and target for one trade given its entry price and the signal-day row."""
    s = STRATEGIES[sid]
    k = s["stop"]
    stop = entry - k[1] * row["atr"] if k[0] == "atr" else row[k[1]]
    t = s["target"]
    if t is None:
        target = np.inf
    elif t[0] == "atr":
        target = entry + t[1] * row["atr"]
    else:
        target = entry + t[2] * row[t[1]]
    return float(stop), float(target)

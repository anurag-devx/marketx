"""Technical setup detectors. Each is independent, configurable and returns one boolean per row.
A stock may pass several. NaN inputs (warm-up, gaps) never pass.

To add a setup: write a function (feat, params, ctx) -> boolean array, add it to SETUPS, and add its
parameter ranges to config.SETUP_SCHEMA. Nothing else changes (screener, evaluation, experiments, UI)."""
import numpy as np, pandas as pd

DESCRIPTIONS = {
    "trend_continuation": "Close > SMA50 > SMA200 and SMA50 rising over the previous 20 sessions; optional RSI band and ADX floor.",
    "breakout_volume": "Close above the highest high of the previous 20 completed sessions (today excluded) on relative volume >= 1.5; optional close > SMA50.",
    "pullback_recovery": "Close > SMA200, SMA50 > SMA200, and RSI14 crossed up through 45 today (yesterday below, today at or above); optional distance to SMA20/50.",
    "relative_strength": "63- or 126-session return (vs the Nifty if available) in the top percentile band of the eligible universe that day.",
    "volatility_breakout": "Bollinger width was in its lowest 20% of the trailing 252 sessions yesterday, and today closes above the previous 20-session high; optional volume.",
}


def _b(x):
    return np.asarray(pd.Series(x).fillna(False), dtype=bool)


def trend_continuation(f, p, ctx):
    cond = (f.close > f.sma50) & (f.sma50 > f.sma200)
    lb = int(p["slope_lookback"])
    if lb == 20:
        slope = f.sma50_slope_20d > 0
    else:
        slope = f.sma50 > f.groupby("symbol", sort=False).sma50.shift(lb)
    cond &= slope
    if p["use_rsi"]:
        cond &= (f.rsi14 >= p["rsi_min"]) & (f.rsi14 <= p["rsi_max"])
    if p["use_adx"]:
        cond &= f.adx14 > p["adx_min"]
    return _b(cond)


def _prev_high(f, n):
    col = f"prev_high_{n}"
    if col in f:
        return f[col]
    return f.groupby("symbol", sort=False).high.transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())


def breakout_volume(f, p, ctx):
    cond = (f.close > _prev_high(f, int(p["lookback"]))) & (f.rel_volume_20d >= p["rel_volume_min"])
    if p["require_above_sma50"]:
        cond &= f.close > f.sma50
    return _b(cond)


def pullback_recovery(f, p, ctx):
    lvl = p["rsi_cross"]
    cond = (f.close > f.sma200) & (f.sma50 > f.sma200) & (f.rsi14_prev < lvl) & (f.rsi14 >= lvl)
    if p["use_proximity"]:
        ma = f[p["proximity_ma"]]
        cond &= (100 * (f.close / ma - 1)).abs() <= p["proximity_pct"]
    return _b(cond)


def relative_strength(f, p, ctx):
    """Percentile among rows that are eligible on the same date (point in time)."""
    w = int(p["return_window"])
    col = f"rel_return_{w}" if p["use_benchmark_relative"] and f[f"rel_return_{w}"].notna().any() else f"return_{w}d"
    pct = ctx.get(f"pctile:{col}")
    if pct is None:
        pct = pctile_by_date(f, col, ctx["eligible"])
        ctx[f"pctile:{col}"] = pct
    return _b(pct >= p["min_percentile"])


def volatility_breakout(f, p, ctx):
    prev_pct = f.groupby("symbol", sort=False).bb_width_pctile_252.shift(1)
    cond = (prev_pct <= p["contraction_pctile_max"]) & (f.close > _prev_high(f, int(p["breakout_lookback"])))
    if p["use_rel_volume"]:
        cond &= f.rel_volume_20d >= p["rel_volume_min"]
    return _b(cond)


SETUPS = {"trend_continuation": trend_continuation, "breakout_volume": breakout_volume, "pullback_recovery": pullback_recovery,
          "relative_strength": relative_strength, "volatility_breakout": volatility_breakout}


def pctile_by_date(f, col, eligible):
    """0-100 percentile of `col` among eligible rows on each date; NaN for ineligible rows."""
    v = f[col].where(np.asarray(eligible, dtype=bool))
    return (v.groupby(f.date).rank(pct=True) * 100).to_numpy()


def evaluate(f, setups_cfg, eligible, ctx=None):
    """Returns a DataFrame with one <setup>_pass column per enabled setup (disabled ones are False)."""
    ctx = ctx if ctx is not None else {}
    ctx["eligible"] = eligible
    out = pd.DataFrame(index=f.index)
    for name, fn in SETUPS.items():
        p = setups_cfg[name]
        out[f"{name}_pass"] = fn(f, p, ctx) & np.asarray(eligible, dtype=bool) if p["enabled"] else False
    return out


def combine(flags, cfg):
    """Qualification rule across enabled setups: any / all / at least k."""
    on = [s for s, p in cfg["setups"].items() if p["enabled"]]
    m = flags[[f"{s}_pass" for s in on]].to_numpy(dtype=bool)
    n = m.sum(axis=1)
    if cfg["combine"] == "all":
        q = n == len(on)
    elif cfg["combine"] == "min_k":
        q = n >= int(cfg["min_setups"])
    else:
        q = n >= 1
    return q, n

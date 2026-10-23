"""Screener features. One row per (symbol, session the stock actually traded).

Conventions (all calculations are grouped by symbol, so stocks never mix):
- Rows are the stock's own bars, oldest first. Missing sessions are NOT forward-filled: "n sessions ago"
  means n of the stock's own bars ago. valid_ratio_60d tells you how many sessions were missing.
- Every value at row t uses bars up to and including the close of t only (no future data). Values that
  must exclude today say so (prev_high_20, rel_volume_20d, median_value_20d_cr).
- Warm-up: a value is NaN until its window is full (e.g. sma200 needs 200 bars, ADX14 needs 27,
  bb_width_pctile_252 needs 19 + 252 bars). NaN never passes a filter or a setup.

Definitions:
  sma20/50/200          simple average of close
  sma50_slope_20d       sma50(t) / sma50(t-20) - 1, in %          ("rising" = > 0)
  rsi14                 Wilder RSI: first average = simple mean of the first 14 changes, then
                        avg = (prev * 13 + current) / 14
  adx14                 Wilder ADX (+DM/-DM/TR smoothed as above; ADX = Wilder average of DX)
  vol_sma20             average volume of the last 20 bars including today
  rel_volume_20d        volume(t) / average volume of the previous 20 bars (t excluded)
  prev_high_20          highest high of the previous 20 bars (t excluded); prev_high_<n> for other n
  return_63d/126d       close(t) / close(t-n) - 1, in %
  bench_return_<n>      Nifty close(t) / Nifty close(on the date of the stock's bar t-n) - 1, in %
  rel_return_<n>        return_<n>d - bench_return_<n> (same dates)
  bb_width              (upper - lower) / middle = 4 x population std(20) / sma20, in %
  bb_width_pctile_252   % of the trailing 252 bb_width values (today included) that are <= today's
  median_value_20d_cr   median of close x volume over the previous 20 bars (t excluded), ₹ crore
  valid_ratio_60d       bars present / 60 expected sessions (Nifty trading days) ending at t
"""
import numpy as np, pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

FEATURE_COLS = ["history_bars", "sma20", "sma50", "sma200", "sma50_slope_20d", "rsi14", "rsi14_prev", "adx14",
                "vol_sma20", "rel_volume_20d", "prev_high_20", "return_63d", "return_126d", "bench_return_63",
                "bench_return_126", "rel_return_63", "rel_return_126", "bb_width", "bb_width_pctile_252",
                "median_value_20d_cr", "valid_ratio_60d", "value_cr"]


def wilder(x, n):
    """Wilder smoothing of a 1-D array: seed = mean of the first n valid values, then (prev*(n-1)+x)/n.
    NaN before the seed and wherever the input is NaN after a gap restarts nothing (inputs have no NaN
    after their first valid value in this module)."""
    out = np.full(len(x), np.nan)
    valid = np.flatnonzero(~np.isnan(x))
    if len(valid) < n:
        return out
    s = valid[0]
    if np.isnan(x[s:s + n]).any():
        return out
    avg = x[s:s + n].mean()
    out[s + n - 1] = avg
    for i in range(s + n, len(x)):
        v = x[i]
        if v != v:
            continue
        avg = (avg * (n - 1) + v) / n
        out[i] = avg
    return out


def _rsi(close, n=14):
    d = np.diff(close, prepend=np.nan)
    up = np.where(np.isnan(d), np.nan, np.clip(d, 0, None))
    dn = np.where(np.isnan(d), np.nan, np.clip(-d, 0, None))
    au, ad = wilder(up, n), wilder(dn, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = au / ad
        r = 100 - 100 / (1 + rs)
    r = np.where((ad == 0) & (au > 0), 100.0, r)
    r = np.where((ad == 0) & (au == 0), 50.0, r)
    return np.where(np.isnan(au), np.nan, r)


def _adx(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[0] = np.nan                                            # first bar has no previous close
    upm = h - np.r_[np.nan, h[:-1]]
    dnm = np.r_[np.nan, l[:-1]] - l
    pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
    mdm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
    pdm[0] = mdm[0] = np.nan
    atr, sp, sm = wilder(tr, n), wilder(pdm, n), wilder(mdm, n)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi, mdi = 100 * sp / atr, 100 * sm / atr
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.where((pdi + mdi) == 0, 0.0, dx)
    dx = np.where(np.isnan(atr), np.nan, dx)
    return wilder(dx, n)


def _pctile_trailing(x, w):
    """% of the trailing w values (current included) that are <= the current value; NaN until w valid values."""
    out = np.full(len(x), np.nan)
    if len(x) < w:
        return out
    win = sliding_window_view(x, w)
    cur = x[w - 1:]
    ok = ~np.isnan(win).any(axis=1)
    pct = 100 * (win <= cur[:, None]).sum(axis=1) / w
    out[w - 1:] = np.where(ok, pct, np.nan)
    return out


def per_symbol(g, cal_pos):
    """g: one stock's bars sorted by date (columns date, open, high, low, close, volume)."""
    c, h, l, v = (g[k].to_numpy(float) for k in ("close", "high", "low", "volume"))
    s = lambda a: pd.Series(a)
    cs, vs = s(c), s(v)
    out = {"history_bars": np.arange(1, len(g) + 1)}
    for n in (20, 50, 200):
        out[f"sma{n}"] = cs.rolling(n, min_periods=n).mean().to_numpy()
    sma50 = s(out["sma50"])
    out["sma50_slope_20d"] = (100 * (sma50 / sma50.shift(20) - 1)).to_numpy()
    out["rsi14"] = _rsi(c, 14)
    out["rsi14_prev"] = np.r_[np.nan, out["rsi14"][:-1]]
    out["adx14"] = _adx(h, l, c, 14)
    out["vol_sma20"] = vs.rolling(20, min_periods=20).mean().to_numpy()
    out["rel_volume_20d"] = (vs / vs.shift(1).rolling(20, min_periods=20).mean().replace(0, np.nan)).to_numpy()
    hs = s(h)
    for n in (20, 55):
        out[f"prev_high_{n}"] = hs.shift(1).rolling(n, min_periods=n).max().to_numpy()
    for n in (63, 126):
        out[f"return_{n}d"] = (100 * (cs / cs.shift(n) - 1)).to_numpy()
    std = cs.rolling(20, min_periods=20).std(ddof=0)
    out["bb_width"] = (100 * 4 * std / s(out["sma20"])).to_numpy()
    out["bb_width_pctile_252"] = _pctile_trailing(out["bb_width"], 252)
    val = cs * vs / 1e7
    out["value_cr"] = val.to_numpy()
    out["median_value_20d_cr"] = val.shift(1).rolling(20, min_periods=20).median().to_numpy()
    # valid observations: own bars within the last 60 expected sessions (calendar positions)
    p = cal_pos
    first = np.searchsorted(p, p - 59, side="left")
    out["valid_ratio_60d"] = np.where(p >= 59, (np.arange(len(p)) - first + 1) / 60.0, np.nan)
    return pd.DataFrame(out, index=g.index)


def build(px, bench=None):
    """px: date, symbol, open, high, low, close, volume (+ anything else, kept). bench: date, nifty.
    Returns px with FEATURE_COLS added, sorted by symbol then date."""
    px = px.sort_values(["symbol", "date"]).reset_index(drop=True)
    if bench is not None and len(bench):
        b = bench.dropna(subset=["nifty"]).sort_values("date")
        cal = pd.DatetimeIndex(b.date.unique())
    else:
        b, cal = None, pd.DatetimeIndex(sorted(px.date.unique()))
    cal_arr = cal.to_numpy()
    parts = []
    for _, g in px.groupby("symbol", sort=False):
        pos = np.searchsorted(cal_arr, g.date.to_numpy())
        parts.append(per_symbol(g, pos))
    f = pd.concat(parts)
    px = pd.concat([px, f], axis=1)
    if b is not None:
        nm = b.set_index("date").nifty
        nifty_t = px.date.map(nm)
        for n in (63, 126):
            past_date = px.groupby("symbol", sort=False).date.shift(n)
            px[f"bench_return_{n}"] = 100 * (nifty_t / past_date.map(nm) - 1)
            px[f"rel_return_{n}"] = px[f"return_{n}d"] - px[f"bench_return_{n}"]
    else:
        for n in (63, 126):
            px[f"bench_return_{n}"] = np.nan
            px[f"rel_return_{n}"] = np.nan
    return px

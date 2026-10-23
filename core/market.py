"""Market highlights computed from the downloaded prices, for any date in the history."""
import numpy as np, pandas as pd


def prepare(px):
    """Per-stock daily indicators for the whole history (computed once; snapshots are then cheap)."""
    p = px.sort_values(["symbol", "date"]).copy()
    g = p.groupby("symbol")
    for n, col in ((1, "r1"), (5, "r5"), (21, "r21")):
        p[col] = 100 * (p.close / g.close.shift(n) - 1)
    p["vol_ratio"] = p.volume / g.volume.transform(lambda s: s.rolling(20).mean().shift(1))
    p["hi252"] = g.high.transform(lambda s: s.rolling(252, min_periods=200).max())
    p["lo252"] = g.low.transform(lambda s: s.rolling(252, min_periods=200).min())
    p["sma200"] = g.close.transform(lambda s: s.rolling(200).mean())
    p["turnover_cr"] = (p.close * p.volume) / 1e7
    p["above200"] = (p.close > p.sma200).astype(float).where(p.sma200.notna())
    return p


def breadth(p):
    """% of stocks above their 200-day average, every day."""
    return p.dropna(subset=["above200"]).groupby("date").above200.mean().mul(100).rename("pct_above_200").reset_index()


def snapshot(p, universe, as_of=None):
    """Stocks, sectors and summary on `as_of` (latest trading day on or before it; default the latest)."""
    dates = np.sort(p.date.unique())
    day = pd.Timestamp(dates[-1]) if as_of is None else pd.Timestamp(dates[dates <= np.datetime64(pd.Timestamp(as_of))][-1])
    d = p[p.date == day].merge(universe.rename(columns={"Symbol": "symbol", "Industry": "sector", "Company Name": "company"}),
                               on="symbol", how="left")
    d["new_high"] = d.high >= d.hi252
    d["new_low"] = d.low <= d.lo252
    sec = d.groupby("sector").agg(stocks=("symbol", "count"), day=("r1", "mean"), week=("r5", "mean"), month=("r21", "mean")).round(2)
    a200 = d.above200.dropna()
    summary = dict(date=day, stocks=len(d), up=int((d.r1 > 0).sum()), down=int((d.r1 < 0).sum()),
                   up1=int((d.r1 > 1).sum()), down1=int((d.r1 < -1).sum()),
                   new_highs=int(d.new_high.sum()), new_lows=int(d.new_low.sum()),
                   above200=float(100 * a200.mean()) if len(a200) else float("nan"), median_day=float(d.r1.median()))
    return d, sec.reset_index(), summary


def highlights(px, universe):
    """Kept for older callers: latest day."""
    p = prepare(px)
    d, sec, s = snapshot(p, universe)
    b = breadth(p)
    return d, sec, b[b.date >= s["date"] - pd.Timedelta(days=400)], s

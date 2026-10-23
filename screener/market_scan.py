"""Market data screener: what moved on a given day, using only data up to that day's close.

Columns per stock (bars are the stock's own sessions; nothing is forward-filled):
  change_1d_pct   close vs previous close          gap_pct       open vs previous close
  change_5d_pct   close vs close 5 bars ago        change_1m_pct close vs 21 bars ago     change_3m_pct  63 bars
  rel_volume      volume / average of the previous 20 bars
  from_52w_high_pct, from_52w_low_pct   close vs highest high / lowest low of the last 252 bars (today included)
  new_52w_high / new_52w_low            today's high / low beyond the previous 252 bars' extreme
  above_sma50 / above_sma200, rsi14, turnover_cr (today), median_value_20d_cr (liquidity)
Presets are plain filters plus a sort; the result is a list of stocks to look at, not a recommendation."""
import numpy as np, pandas as pd

PRESETS = {
    "Top gainers": dict(sort="change_1d_pct", ascending=False, where=lambda d: d.change_1d_pct > 0),
    "Top losers": dict(sort="change_1d_pct", ascending=True, where=lambda d: d.change_1d_pct < 0),
    "Gap up (≥ 2%)": dict(sort="gap_pct", ascending=False, where=lambda d: d.gap_pct >= 2),
    "Gap down (≤ -2%)": dict(sort="gap_pct", ascending=True, where=lambda d: d.gap_pct <= -2),
    "Volume spike (≥ 2x)": dict(sort="rel_volume", ascending=False, where=lambda d: d.rel_volume >= 2),
    "New 52-week high": dict(sort="change_1d_pct", ascending=False, where=lambda d: d.new_52w_high),
    "New 52-week low": dict(sort="change_1d_pct", ascending=True, where=lambda d: d.new_52w_low),
    "Within 3% of 52-week high": dict(sort="from_52w_high_pct", ascending=False, where=lambda d: d.from_52w_high_pct >= -3),
    "Oversold (RSI < 30)": dict(sort="rsi14", ascending=True, where=lambda d: d.rsi14 < 30),
    "Overbought (RSI > 70)": dict(sort="rsi14", ascending=False, where=lambda d: d.rsi14 > 70),
    "Strongest 3 months": dict(sort="change_3m_pct", ascending=False, where=lambda d: d.change_3m_pct.notna()),
    "Weakest 3 months": dict(sort="change_3m_pct", ascending=True, where=lambda d: d.change_3m_pct.notna()),
    "All stocks": dict(sort="turnover_cr", ascending=False, where=lambda d: d.close.notna()),
}
COLS = ["symbol", "company_name", "sector", "close", "change_1d_pct", "gap_pct", "change_5d_pct", "change_1m_pct", "change_3m_pct",
        "rel_volume", "from_52w_high_pct", "from_52w_low_pct", "new_52w_high", "new_52w_low", "rsi14", "above_sma50", "above_sma200",
        "turnover_cr", "median_value_20d_cr"]


def snapshot(feat, date=None):
    """One row per stock that traded on `date` (default: the latest date)."""
    d = pd.Timestamp(date) if date is not None else feat.date.max()
    w = feat[(feat.date <= d) & (feat.date > d - pd.Timedelta(days=420))].sort_values(["symbol", "date"])
    g = w.groupby("symbol", sort=False)
    pc = g.close.shift(1)
    out = pd.DataFrame(index=w.index)
    out["change_1d_pct"] = 100 * (w.close / pc - 1)
    out["gap_pct"] = 100 * (w.open / pc - 1)
    for n, col in ((5, "change_5d_pct"), (21, "change_1m_pct"), (63, "change_3m_pct")):
        out[col] = 100 * (w.close / g.close.shift(n) - 1)
    hi = g.high.transform(lambda s: s.rolling(252, min_periods=252).max())
    lo = g.low.transform(lambda s: s.rolling(252, min_periods=252).min())
    phi = g.high.transform(lambda s: s.shift(1).rolling(252, min_periods=252).max())
    plo = g.low.transform(lambda s: s.shift(1).rolling(252, min_periods=252).min())
    out["from_52w_high_pct"] = 100 * (w.close / hi - 1)
    out["from_52w_low_pct"] = 100 * (w.close / lo - 1)
    out["new_52w_high"] = (w.high > phi).fillna(False)
    out["new_52w_low"] = (w.low < plo).fillna(False)
    vol_avg = g.volume.transform(lambda s: s.shift(1).rolling(20, min_periods=20).mean())
    out["rel_volume"] = w.volume / vol_avg.replace(0, np.nan)
    out["turnover_cr"] = w.close * w.volume / 1e7
    w = pd.concat([w, out], axis=1)
    today = w[w.date == d].copy()
    today["above_sma50"] = today.close > today.get("sma50")
    today["above_sma200"] = today.close > today.get("sma200")
    for c in ("company_name", "sector", "rsi14", "median_value_20d_cr"):
        if c not in today:
            today[c] = np.nan
    return today[COLS].reset_index(drop=True), d


def scan(snap, preset="Top gainers", min_price=0.0, min_turnover_cr=0.0, sectors=(), symbols=None, limit=100):
    """Applies a preset and the common filters. Returns (rows, counts)."""
    p = PRESETS[preset]
    d = snap
    counts = {"stocks that traded": len(d)}
    d = d[(d.close >= min_price) & (d.median_value_20d_cr.fillna(0) >= min_turnover_cr)]
    counts["after price and liquidity"] = len(d)
    if sectors:
        d = d[d.sector.isin(sectors)]
        counts["in chosen sectors"] = len(d)
    if symbols is not None:
        d = d[d.symbol.isin(symbols)]
        counts["in chosen list"] = len(d)
    d = d[p["where"](d).fillna(False).astype(bool)]
    counts[f"match: {preset}"] = len(d)
    d = d.sort_values(p["sort"], ascending=p["ascending"], na_position="last")
    return (d.head(limit) if limit else d).reset_index(drop=True), counts


def sector_summary(snap):
    s = snap.groupby("sector").agg(stocks=("symbol", "count"), avg_change_1d_pct=("change_1d_pct", "mean"),
                                   advancing=("change_1d_pct", lambda x: int((x > 0).sum())), declining=("change_1d_pct", lambda x: int((x < 0).sum())),
                                   avg_change_1m_pct=("change_1m_pct", "mean"), new_highs=("new_52w_high", "sum"), new_lows=("new_52w_low", "sum"))
    return s.sort_values("avg_change_1d_pct", ascending=False).reset_index()

"""Point-in-time universe: which stocks a test may trade on each date, using only what was known then.

Testing on today's Nifty 500 is survivorship-biased: it contains the stocks that did well enough to be
in the index today, and misses the ones that fell out. Two ways to fix it:

1. Rule-based (default): at each rebalance (end of March and September, like NSE's semi-annual review),
   take the top N stocks by 6-month average daily traded value among every stock we have data for,
   with at least 6 months of history and price >= MIN_PRICE. Membership holds until the next review.
   Needs the broad download (python3 fetch_angel.py --broad) to include stocks outside today's list.
   Stocks that were delisted (no longer on NSE) are still missing: Angel has no data for them.
2. Official lists: put NSE's historical constituents in data/membership.csv
   (symbol, from_date, to_date; empty to_date = still a member). Used instead of the rule when present.

Traded value is a proxy for NSE's free-float market cap ranking; the overlap with the real Nifty 500 is
typically 85-90%."""
import numpy as np, pandas as pd
from .config import DATA, MIN_PRICE

MEMBERSHIP = DATA / "membership.csv"


def review_dates(dates):
    """Last trading day of March and September in the data."""
    d = pd.Series(pd.DatetimeIndex(sorted(set(dates))))
    m = d[d.dt.month.isin([3, 9])]
    return list(m.groupby([m.dt.year, m.dt.month]).max())


def rule_based(px, n=500, lookback_days=126):
    """Returns a membership table: symbol, from_date, to_date (exclusive)."""
    p = px[["date", "symbol", "close", "volume"]].sort_values(["symbol", "date"]).copy()
    p["value"] = p.close * p.volume
    g = p.groupby("symbol", sort=False)
    p["avg_value"] = g.value.transform(lambda s: s.rolling(lookback_days, min_periods=lookback_days).mean())
    p["bars"] = g.cumcount() + 1
    reviews = review_dates(p.date)
    last = p.date.max() + pd.Timedelta(days=1)
    rows = []
    for i, r in enumerate(reviews):
        snap = p[(p.date <= r) & (p.date > r - pd.Timedelta(days=10))].sort_values("date").groupby("symbol").tail(1)
        snap = snap[(snap.bars >= lookback_days) & (snap.close >= MIN_PRICE) & snap.avg_value.notna()]
        top = snap.nlargest(n, "avg_value").symbol
        start = r + pd.Timedelta(days=1)
        end = reviews[i + 1] + pd.Timedelta(days=1) if i + 1 < len(reviews) else last
        rows += [dict(symbol=s, from_date=start, to_date=end) for s in top]
    return pd.DataFrame(rows)


def official():
    if not MEMBERSHIP.exists():
        return None
    m = pd.read_csv(MEMBERSHIP)
    m["from_date"] = pd.to_datetime(m.from_date)
    m["to_date"] = pd.to_datetime(m.to_date).fillna(pd.Timestamp("2100-01-01"))
    return m[["symbol", "from_date", "to_date"]]


def membership(px, n=500):
    m = official()
    return (m, "official list (data/membership.csv)") if m is not None else (rule_based(px, n), f"top {n} by traded value, reviewed each March and September")


def mask(dates, symbols, mem):
    """Boolean array: was (symbol, date) in the universe? dates/symbols are aligned arrays."""
    df = pd.DataFrame({"date": pd.to_datetime(dates), "symbol": symbols, "_i": np.arange(len(dates))})
    out = np.zeros(len(df), dtype=bool)
    for sym, g in df.groupby("symbol", sort=False):
        mm = mem[mem.symbol == sym]
        if mm.empty:
            continue
        d = g.date.to_numpy()
        hit = np.zeros(len(g), dtype=bool)
        for f, t in zip(mm.from_date.to_numpy(), mm.to_date.to_numpy()):
            hit |= (d >= f) & (d < t)
        out[g._i.to_numpy()] = hit
    return out


def overlap_with_today(mem, today_symbols):
    """Share of the latest point-in-time universe that is in today's official list (a sanity check)."""
    if mem is None or mem.empty:
        return None
    latest = mem[mem.from_date == mem.from_date.max()].symbol
    return float(np.mean([s in set(today_symbols) for s in latest]))

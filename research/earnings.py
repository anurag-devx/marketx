"""Quarterly results as analysis: growth, surprise vs the company's own history, and how prices reacted.

quarters()  one row per stock and quarter, as first announced (the filing the market reacted to), on one basis:
            consolidated when the company files it, else standalone. A comparison is only made between quarters on
            the SAME basis; otherwise it is left blank.
  revenue_cr, profit_cr      ₹ crore. profit = attributable to the parent's owners when reported, else net profit
  ebitda_cr                  proxy: profit before tax + finance costs + depreciation - other income (blank if a part is missing)
  margin_pct, ebitda_margin  on revenue
  revenue_yoy, profit_yoy    vs the same quarter a year earlier (profit growth left blank when the earlier profit was a loss)
  sue_profit, sue_revenue    standardised unexpected result: (this quarter - same quarter last year) divided by the
                             standard deviation of that change over the previous 8 quarters (at least 4). Free sources have
                             no analyst consensus, so "surprise" here means against the company's own trend, not
                             against estimates. Known in finance research as SUE; the price drift after it as
                             post-earnings-announcement drift.
  day0, react_rel            first trading day the market could react (after 15:30 -> next day) and that day's return minus
                             the Nifty's; drift_5 / drift_20: the following 5 / 20 sessions minus the Nifty (from day-0 close)

drift_study()  average reaction and drift by surprise bucket, with t-statistics and hit rates, split into an earlier
               period and the most recent quarters (held out) so a pattern that only exists in-sample is visible."""
import numpy as np, pandas as pd

CR = 1e7
BUCKETS = [(-np.inf, -2, "big miss (SUE < -2)"), (-2, -0.5, "miss (-2 to -0.5)"), (-0.5, 0.5, "in line (-0.5 to 0.5)"),
           (0.5, 2, "beat (0.5 to 2)"), (2, np.inf, "big beat (SUE > 2)")]


def _first_filings(res):
    r = res[res.status == "ok"].copy()
    if r.empty:
        return r
    r["period_end"] = pd.to_datetime(r.period_end)
    r["filed_at"] = pd.to_datetime(r.filed_at)
    has_cons = r[r.consolidated.astype(bool)].groupby(["symbol", "period_end"]).size()
    r["basis"] = np.where(r.consolidated.astype(bool), "consolidated", "standalone")
    keep = [(c or (s, p) not in has_cons.index) for s, p, c in zip(r.symbol, r.period_end, r.consolidated.astype(bool))]
    r = r[keep].sort_values("filed_at")
    first = r.drop_duplicates(["symbol", "period_end"], keep="first").copy()
    n = r.groupby(["symbol", "period_end"]).size()
    first["revised"] = [n.get((s, p), 1) > 1 for s, p in zip(first.symbol, first.period_end)]
    return first


def quarters(res=None):
    if res is None:
        from core import fundamentals
        res = fundamentals.results()
    q = _first_filings(res)
    if q.empty:
        return pd.DataFrame()
    num = lambda c: pd.to_numeric(q[c], errors="coerce")
    q["revenue_cr"] = num("revenue") / CR
    prof = num("profit_owners").where(num("profit_owners").notna(), num("profit"))
    q["profit_cr"] = prof / CR
    q["ebitda_cr"] = (num("pbt") + num("finance_costs") + num("depreciation") - num("other_income").fillna(0)) / CR
    q["eps"] = num("eps_basic")
    rev = q.revenue_cr.where(q.revenue_cr > 0)
    q["margin_pct"] = 100 * q.profit_cr / rev
    q["ebitda_margin"] = 100 * q.ebitda_cr / rev
    q = q.sort_values(["symbol", "period_end"]).reset_index(drop=True)
    out = []
    for sym, g in q.groupby("symbol", sort=False):
        g = g.set_index("period_end").sort_index()
        prev = g.index - pd.DateOffset(years=1)
        def lag(col):
            vals, basis = [], []
            for p in prev:
                m = g.index[(g.index >= p - pd.Timedelta(days=5)) & (g.index <= p + pd.Timedelta(days=5))]
                vals.append(g.loc[m[0], col] if len(m) else np.nan)
                basis.append(g.loc[m[0], "basis"] if len(m) else None)
            return pd.Series(vals, index=g.index), pd.Series(basis, index=g.index)
        for col, name in (("revenue_cr", "revenue"), ("profit_cr", "profit")):
            ly, lb = lag(col)
            same = lb == g.basis
            g[f"{name}_ly"] = ly.where(same)
            g[f"{name}_yoy"] = np.where(same & (ly > 0), 100 * (g[col] / ly - 1), np.nan)
            ue = (g[col] - ly).where(same)
            sd = ue.shift(1).rolling(8, min_periods=4).std()
            g[f"sue_{name}"] = (ue / sd.replace(0, np.nan)).clip(-10, 10)
        g["quarter"] = [f"Q{((p.month - 4) % 12) // 3 + 1} FY{(p.year + (1 if p.month > 3 else 0)) % 100:02d}" for p in g.index]
        out.append(g.reset_index())
    q = pd.concat(out, ignore_index=True)
    for c in ("revenue_ly", "revenue_yoy", "sue_revenue", "profit_ly", "profit_yoy", "sue_profit"):
        q[c] = pd.to_numeric(q[c], errors="coerce").astype(float)
    return q


def add_reaction(q, px=None, idx=None):
    """day0, react_rel, drift_5, drift_20 (stock minus Nifty, percent)."""
    from research.events import event_day
    if px is None or idx is None:
        from core.data import load_raw
        px, idx, _ = load_raw()
    if q.empty:
        return q
    px = px[["date", "symbol", "close"]].copy()
    px["date"] = pd.to_datetime(px.date)
    idx = idx[["date", "nifty"]].dropna().copy()
    idx["date"] = pd.to_datetime(idx.date)
    dates = pd.DatetimeIndex(sorted(idx.date.unique()))
    nif = idx.set_index("date").nifty
    close = {s: g.set_index("date").close.sort_index() for s, g in px.groupby("symbol")}
    rows = []
    for r in q.itertuples():
        d0 = event_day(r.filed_at, dates)
        c = close.get(r.symbol)
        res = dict(day0=d0, react_rel=np.nan, drift_5=np.nan, drift_20=np.nan)
        if c is not None and not pd.isna(d0) and d0 in c.index:
            i = c.index.get_loc(d0)
            if i > 0 and d0 in nif.index:
                prev = c.index[i - 1]
                if prev in nif.index:
                    res["react_rel"] = 100 * ((c.iloc[i] / c.iloc[i - 1] - 1) - (nif[d0] / nif[prev] - 1))
            for h in (5, 20):
                if i + h < len(c):
                    e = c.index[i + h]
                    if e in nif.index:
                        res[f"drift_{h}"] = 100 * ((c.iloc[i + h] / c.iloc[i] - 1) - (nif[e] / nif[d0] - 1))
        rows.append(res)
    return pd.concat([q.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def bucket(sue):
    for lo, hi, lab in BUCKETS:
        if lo <= sue < hi:
            return lab
    return None


def drift_study(qr, holdout_quarters=4, min_events=20):
    """By profit-surprise bucket: events, mean reaction, mean 5 / 20-day drift (stock minus Nifty, %), t-stat, hit rate,
    separately for the earlier period and the latest `holdout_quarters` quarter ends."""
    d = qr.dropna(subset=["sue_profit"]).copy()
    if d.empty:
        return pd.DataFrame()
    d["bucket"] = d.sue_profit.map(bucket)
    ends = sorted(d.period_end.unique())
    cut = ends[-holdout_quarters] if len(ends) > holdout_quarters else ends[0]
    d["sample"] = np.where(d.period_end >= cut, f"latest {holdout_quarters} quarters (held out)", "earlier")
    rows = []
    order = [b[2] for b in BUCKETS]
    for (smp, b), g in d.groupby(["sample", "bucket"]):
        row = dict(sample=smp, surprise=b, events=len(g))
        for c in ("react_rel", "drift_5", "drift_20"):
            x = g[c].dropna()
            row[f"{c}_mean"] = x.mean() if len(x) else np.nan
            row[f"{c}_t"] = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else np.nan
            row[f"{c}_hit"] = 100 * (x > 0).mean() if len(x) else np.nan
        row["enough"] = len(g) >= min_events
        rows.append(row)
    out = pd.DataFrame(rows)
    out["o"] = out.surprise.map(order.index)
    return out.sort_values(["sample", "o"]).drop(columns="o").reset_index(drop=True)


def latest(qr, days=30):
    """Results filed in the last `days` days, newest first."""
    if qr.empty:
        return qr
    return qr[qr.filed_at >= pd.Timestamp.now() - pd.Timedelta(days=days)].sort_values("filed_at", ascending=False)


def build(log=print):
    """Everything the screens need, in one cached frame: data/fundamentals/quarters.parquet."""
    from core import fundamentals, atomic
    q = quarters()
    if q.empty:
        log("No results read yet.")
        return q
    q = add_reaction(q)
    atomic.to_parquet(q, fundamentals.DIR / "quarters.parquet")
    log(f"Quarterly table: {q.symbol.nunique()} stocks, {len(q)} stock-quarters, SUE available for {q.sue_profit.notna().sum()}.")
    return q


if __name__ == "__main__":
    build()

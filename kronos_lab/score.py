"""Scoring Kronos forecasts against what actually happened. Pure pandas (no torch): the web page uses it.

A forecast is scored once its h sessions have passed (using the stored daily prices). Questions answered:
  Direction   does the predicted sign beat simply guessing the more common outcome?
  Probability is P(up) better than the base rate? (Brier score, lower is better; skill > 0 = better)
  Ranking     on each day, do stocks the model ranks higher actually do better? (rank correlation, 'IC')
  Spread      top fifth minus bottom fifth of the model's ranking, in actual returns vs the Nifty
t-statistics use one value per day and only days at least h sessions apart, so overlapping windows aren't counted twice.
The base rate is the share of scored forecasts that went up: measured on the same sample, so it slightly flatters it."""
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
FILE = BASE / "results" / "kronos" / "forecasts.parquet"


def forecasts():
    if not FILE.exists():
        return pd.DataFrame()
    d = pd.read_parquet(FILE)
    d["as_of"] = pd.to_datetime(d.as_of)
    return d


def outcomes(fc, px, index=None):
    """Adds realized_ret_pct, nifty_ret_pct, excess_pct and end_date to forecasts whose horizon has passed."""
    if fc.empty:
        return fc.assign(realized_ret_pct=np.nan, nifty_ret_pct=np.nan, excess_pct=np.nan, end_date=pd.NaT)
    close = px.pivot_table(index="date", columns="symbol", values="close").sort_index()
    dates = close.index
    pos = pd.Series(np.arange(len(dates)), index=dates)
    nifty = index.set_index("date").nifty.reindex(dates).ffill() if index is not None and len(index) else None
    out = []
    for r in fc.itertuples():
        i = pos.get(r.as_of)
        j = None if i is None else i + int(r.h)
        if j is None or j >= len(dates) or r.symbol not in close:
            out.append((np.nan, np.nan, pd.NaT))
            continue
        c1 = close[r.symbol].iat[j]
        real = 100 * (c1 / r.close_asof - 1) if pd.notna(c1) else np.nan
        nr = 100 * (nifty.iat[j] / nifty.iat[i] - 1) if nifty is not None and pd.notna(nifty.iat[i]) else np.nan
        out.append((real, nr, dates[j]))
    d = fc.copy()
    d["realized_ret_pct"], d["nifty_ret_pct"], d["end_date"] = zip(*out)
    d["excess_pct"] = d.realized_ret_pct - d.nifty_ret_pct
    return d


def _t(x):
    x = pd.Series(x).dropna()
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) >= 3 and x.std(ddof=1) > 0 else np.nan


def _spaced(days, h):
    keep, last = [], None
    for d in sorted(days):
        if last is None or (d - last).days >= h * 7 / 5:
            keep.append(d); last = d
    return keep


def summary(scored):
    s = scored.dropna(subset=["realized_ret_pct"])
    if s.empty:
        return None
    rows = []
    for (m, h), g in s.groupby(["model", "h"]):
        up = (g.realized_ret_pct > 0)
        base = up.mean()
        hit = ((g.pred_ret_pct > 0) == up).mean()
        brier = ((g.p_up - up) ** 2).mean()
        brier_base = ((base - up) ** 2).mean()
        per_day = []
        for d, x in g.groupby("as_of"):
            if len(x) >= 10:
                q = x.pred_ret_pct.rank(pct=True)
                per_day.append(dict(as_of=d, ic=x.pred_ret_pct.corr(x.realized_ret_pct, method="spearman"),
                                    spread=x[q > 0.8].excess_pct.mean() - x[q <= 0.2].excess_pct.mean(), n=len(x)))
        pdd = pd.DataFrame(per_day)
        sp = pdd[pdd.as_of.isin(_spaced(pdd.as_of, h))] if len(pdd) else pdd
        rows.append(dict(model=f"Kronos-{m}", h=int(h), forecasts=len(g), days=g.as_of.nunique(), stocks=g.symbol.nunique(),
                         share_up=100 * base, hit_rate=100 * hit, best_guess=100 * max(base, 1 - base),
                         brier=brier, brier_base=brier_base, skill=1 - brier / brier_base if brier_base > 0 else np.nan,
                         mean_ic=pdd.ic.mean() if len(pdd) else np.nan, ic_t=_t(sp.ic) if len(sp) else np.nan,
                         spread_pct=pdd.spread.mean() if len(pdd) else np.nan, spread_t=_t(sp.spread) if len(sp) else np.nan,
                         independent_days=len(sp)))
    return pd.DataFrame(rows)


def by_day(scored):
    s = scored.dropna(subset=["realized_ret_pct"])
    rows = []
    for (d, m, h), x in s.groupby(["as_of", "model", "h"]):
        if len(x) >= 10:
            q = x.pred_ret_pct.rank(pct=True)
            rows.append(dict(as_of=d, model=m, h=h, stocks=len(x), ic=x.pred_ret_pct.corr(x.realized_ret_pct, method="spearman"),
                             hit_rate=100 * ((x.pred_ret_pct > 0) == (x.realized_ret_pct > 0)).mean(),
                             top_minus_bottom=x[q > 0.8].excess_pct.mean() - x[q <= 0.2].excess_pct.mean()))
    return pd.DataFrame(rows)


def calibration(scored, bins=5):
    s = scored.dropna(subset=["realized_ret_pct"])
    if len(s) < 50:
        return pd.DataFrame()
    b = pd.cut(s.p_up, np.linspace(0, 1, bins + 1), include_lowest=True)
    g = s.groupby(b, observed=True)
    return pd.DataFrame(dict(predicted=g.p_up.mean(), observed=g.realized_ret_pct.apply(lambda r: (r > 0).mean()), forecasts=g.size())).reset_index(drop=True)

"""Scoring Kronos forecasts against what actually happened. Pure pandas (no torch): the web page uses it.

A forecast is scored once its h sessions have passed (using the stored daily prices). Questions answered:
  Direction   does the predicted sign beat simply guessing the more common outcome?
  Probability is P(up) better than the base rate? (Brier score, lower is better; skill > 0 = better)
  Ranking     on each day, do stocks the model ranks higher actually do better? (rank correlation, 'IC')
  Spread      top fifth minus bottom fifth of the model's ranking, in actual returns (the same day, so the market cancels out)
Forecasts are grouped by setting (model, bar size, horizon, temperature, top_p): each setting gets its own score.
5-minute forecasts are scored from data/intraday/5min: the close of the h-th bar after the as-of time.
t-statistics use one value per day and only days at least h sessions apart, so overlapping windows aren't counted twice.
The base rate is the share of scored forecasts that went up: measured on the same sample, so it slightly flatters it."""
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
FILE = BASE / "results" / "kronos" / "forecasts.parquet"


SET = ["model", "freq", "h", "temp", "top_p"]


def forecasts():
    from . import forecast as F
    if not FILE.exists():
        return pd.DataFrame()
    d = F.load()
    d["as_of"] = pd.to_datetime(d.as_of)
    return d


def setting(r):
    """Short label for one setting, e.g. 'small · 5 days · T1.0 p0.9' or 'mini · 12 bars (5-min) · T1.2 p0.9'."""
    unit = "bars (5-min)" if r["freq"] == "5m" else ("day" if int(r["h"]) == 1 else "days")
    return f"{r['model']} · {int(r['h'])} {unit} · T{float(r['temp']):g} p{float(r['top_p']):g}"


def _outcomes_5m(fc):
    from . import forecast as F
    real, ends, cache = [], [], {}
    for r in fc.itertuples():
        if r.symbol not in cache:
            f = F.BARS5 / f"{r.symbol}.parquet"
            cache[r.symbol] = pd.read_parquet(f).assign(ts=lambda x: pd.to_datetime(x.ts)).set_index("ts").close if f.exists() else pd.Series(dtype=float)
        c = cache[r.symbol]
        t = pd.Timestamp(r.as_of) + pd.Timedelta(minutes=5 * (int(r.h) - 1))
        v = c.get(t)
        real.append(100 * (v / r.close_asof - 1) if v is not None and pd.notna(v) else np.nan)
        ends.append(t + pd.Timedelta(minutes=5) if v is not None else pd.NaT)
    return real, ends


def outcomes(fc, px, index=None):
    """Adds realized_ret_pct, nifty_ret_pct, excess_pct and end_date to forecasts whose horizon has passed."""
    if fc.empty:
        return fc.assign(realized_ret_pct=np.nan, nifty_ret_pct=np.nan, excess_pct=np.nan, end_date=pd.NaT)
    if "freq" in fc and (fc.freq == "5m").any():
        m = fc.freq == "5m"
        a, b = outcomes(fc[~m], px, index), fc[m].copy()
        b["realized_ret_pct"], b["end_date"] = _outcomes_5m(b) if len(b) else ([], [])
        b["nifty_ret_pct"], b["excess_pct"] = np.nan, np.nan
        return pd.concat([a, b]).sort_index()
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
    s = _filled(s)
    for key, g in s.groupby(SET):
        m, h = key[0], key[2]
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
                                    spread=x[q > 0.8].realized_ret_pct.mean() - x[q <= 0.2].realized_ret_pct.mean(), n=len(x)))
        pdd = pd.DataFrame(per_day)
        sp = pdd[pdd.as_of.isin(_spaced(pdd.as_of, h if key[1] == "1d" else 0))] if len(pdd) else pdd
        rows.append(dict(setting=setting(dict(zip(SET, key))), **dict(zip(SET, key)), model_name=f"Kronos-{m}", forecasts=len(g), days=g.as_of.nunique(), stocks=g.symbol.nunique(),
                         share_up=100 * base, hit_rate=100 * hit, best_guess=100 * max(base, 1 - base),
                         brier=brier, brier_base=brier_base, skill=1 - brier / brier_base if brier_base > 0 else np.nan,
                         mean_ic=pdd.ic.mean() if len(pdd) else np.nan, ic_t=_t(sp.ic) if len(sp) else np.nan,
                         spread_pct=pdd.spread.mean() if len(pdd) else np.nan, spread_t=_t(sp.spread) if len(sp) else np.nan,
                         independent_days=len(sp)))
    return pd.DataFrame(rows)


def _filled(s):
    from . import forecast as F
    s = s.copy()
    for k, v in F.DEFAULTS.items():
        if v is not None:
            s[k] = s[k].fillna(v) if k in s else v
    return s


def by_day(scored):
    s = _filled(scored.dropna(subset=["realized_ret_pct"]))
    rows = []
    for key, x in s.groupby(["as_of"] + SET):
        if len(x) >= 10:
            q = x.pred_ret_pct.rank(pct=True)
            rows.append(dict(as_of=key[0], setting=setting(dict(zip(SET, key[1:]))), stocks=len(x), ic=x.pred_ret_pct.corr(x.realized_ret_pct, method="spearman"),
                             hit_rate=100 * ((x.pred_ret_pct > 0) == (x.realized_ret_pct > 0)).mean(),
                             top_minus_bottom=x[q > 0.8].realized_ret_pct.mean() - x[q <= 0.2].realized_ret_pct.mean()))
    return pd.DataFrame(rows)


def calibration(scored, bins=5):
    s = scored.dropna(subset=["realized_ret_pct"])
    if len(s) < 50:
        return pd.DataFrame()
    b = pd.cut(s.p_up, np.linspace(0, 1, bins + 1), include_lowest=True)
    g = s.groupby(b, observed=True)
    return pd.DataFrame(dict(predicted=g.p_up.mean(), observed=g.realized_ret_pct.apply(lambda r: (r > 0).mean()), forecasts=g.size())).reset_index(drop=True)

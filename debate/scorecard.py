"""Forecast scorecard: forecasts compared with what actually happened.

All numbers are computed here from data/debate/outcomes.parquet (scored forecasts) and the forecast stores.
Binary targets only (5 sessions vs the Nifty, 5 sessions absolute); three-outcome forecasts are listed but not
aggregated. Every table carries its sample size and an 'enough' flag (MIN_CELL); below it, read the row as anecdote.

  Brier           mean (p - outcome)^2; 0.25 = always saying 50%; lower is better
  base Brier      the same for the historical base rate used at forecast time
  skill           1 - Brier / base Brier (> 0 = better than the base rate); 95% interval resamples whole forecast weeks
                  (forecasts on nearby days overlap, so days are not independent)
  hit rate        share of forecasts where p > 0.5 matched the outcome
  calibration     mean |predicted - observed| over equal-count probability bins (lower is better)"""
import numpy as np, pandas as pd
from . import probability as PR

MIN_CELL = 30
MIN_HEADLINE = 100


def binary(out):
    if out is None or out.empty:
        return pd.DataFrame()
    out = out.copy()
    for c, dflt in (("source", "debate"), ("mode", "live"), ("list_name", None), ("sector", None), ("regime", None)):
        if c not in out:
            out[c] = dflt
    out["source"] = out.source.fillna("debate")
    out["mode"] = out["mode"].fillna("live")
    o = out[(out.target != "three") & out.p_up.notna() & out.brier.notna()].copy()
    o["y"] = (o.outcome == "positive").astype(float)
    o["hit"] = ((o.p_up > 0.5) == (o.y == 1)).astype(float)
    return o


def headline(o):
    if o.empty:
        return dict(n=0, status="No scored forecasts yet: they are scored once their 5 sessions have passed.")
    r = dict(n=int(len(o)), days=int(o.as_of.nunique()), stocks=int(o.symbol.nunique()), brier=float(o.brier.mean()),
             brier_base=float(o.brier_base.mean()) if o.brier_base.notna().any() else np.nan, logloss=float(o.logloss.mean()),
             hit=float(o.hit.mean()), share_positive=float(o.y.mean()), mean_p=float(o.p_up.mean()),
             mean_return=float(o.realized_return_pct.mean()))
    ok = o.brier_base.notna()
    if ok.sum() >= 10:
        # forecasts made on nearby days share most of their 5-session window, so resample whole WEEKS, not days
        weeks = pd.to_datetime(o.as_of[ok]).dt.to_period("W-FRI").astype(str).to_numpy()
        bss, lo, hi = PR.skill_ci(o.brier[ok].to_numpy(), o.brier_base[ok].to_numpy(), weeks)
        r.update(skill=bss, skill_lo=lo, skill_hi=hi)
    else:
        r.update(skill=np.nan, skill_lo=np.nan, skill_hi=np.nan)
    r["ece"] = PR.reliability(o.p_up.to_numpy(), o.y.to_numpy(), bins=min(10, max(2, len(o) // 50)))[1] if len(o) >= 20 else np.nan
    if r["n"] < MIN_HEADLINE:
        r["status"] = f"Only {r['n']} scored forecasts: too few to judge skill (needs {MIN_HEADLINE}+, ideally 1,000+)."
    elif r["skill_lo"] > 0:
        r["status"] = f"Better than the base rate so far: skill {r['skill']:+.4f} (95% {r['skill_lo']:+.4f} to {r['skill_hi']:+.4f})."
    elif r["skill_hi"] < 0:
        r["status"] = f"Worse than the base rate so far: skill {r['skill']:+.4f} (95% {r['skill_lo']:+.4f} to {r['skill_hi']:+.4f})."
    else:
        r["status"] = f"No measurable skill yet: skill {r['skill']:+.4f}, interval includes 0 ({r['skill_lo']:+.4f} to {r['skill_hi']:+.4f})."
    return r


def _group(o, by):
    rows = []
    for k, g in o.groupby(by, dropna=False):
        ok = g.brier_base.notna()
        bb = g.brier_base[ok].mean() if ok.any() else np.nan
        rows.append({**(dict(zip(by, k)) if isinstance(by, list) else {by: k}), "forecasts": len(g), "brier": g.brier.mean(), "base_brier": bb,
                     "skill": 1 - g.brier[ok].mean() / bb if ok.any() and bb > 0 else np.nan, "hit_rate": g.hit.mean(),
                     "share_positive": g.y.mean(), "mean_p": g.p_up.mean(), "mean_return_pct": g.realized_return_pct.mean(),
                     "enough": len(g) >= MIN_CELL})
    return pd.DataFrame(rows)


def reliability(o, bins=10):
    if len(o) < 20:
        return pd.DataFrame()
    t, _ = PR.reliability(o.p_up.to_numpy(), o.y.to_numpy(), bins=min(bins, max(2, len(o) // 30)))
    return t.rename(columns={"pred": "predicted", "obs": "observed"})


def deciles(o, n=10):
    """Does a higher probability go with better outcomes?"""
    if len(o) < 20:
        return pd.DataFrame()
    q = min(n, max(2, len(o) // 30))
    d = o.assign(bucket=pd.qcut(o.p_up.rank(method="first"), q, labels=False) + 1)
    g = d.groupby("bucket").agg(p_from=("p_up", "min"), p_to=("p_up", "max"), forecasts=("p_up", "size"), share_positive=("y", "mean"),
                                mean_return_pct=("realized_return_pct", "mean"), median_return_pct=("realized_return_pct", "median")).reset_index()
    g["enough"] = g.forecasts >= MIN_CELL
    return g


def over_time(o, freq="W-FRI", window=4):
    """Weekly Brier vs base, by the week the outcome became known, plus a rolling skill."""
    if o.empty:
        return pd.DataFrame()
    d = o.assign(week=pd.to_datetime(o.end_date).dt.to_period(freq).dt.end_time.dt.normalize())
    w = d.groupby("week").agg(forecasts=("brier", "size"), brier=("brier", "mean"), base=("brier_base", "mean"), hit=("hit", "mean")).reset_index()
    w["skill"] = 1 - w.brier / w.base
    s = d.groupby("week").agg(b=("brier", "sum"), bb=("brier_base", "sum"))
    roll = s.rolling(window, min_periods=1).sum()
    w["rolling_skill"] = (1 - roll.b / roll.bb).to_numpy()
    return w


def verdicts(o):
    return _group(o, "verdict")


def misses(o, n=20):
    """Most confident forecasts that were wrong."""
    if o.empty:
        return pd.DataFrame()
    m = o[o.hit == 0].assign(confidence=lambda d: (d.p_up - 0.5).abs()).sort_values("confidence", ascending=False).head(n)
    return m[["as_of", "symbol", "target", "p_up", "base_up", "verdict", "realized_return_pct", "outcome", "source"]]


def head_to_head(o):
    """Debate vs batch on the same (day, stock, target, horizon), plus the base rate."""
    if o.empty or "source" not in o or o.source.nunique() < 2:
        return pd.DataFrame()
    k = ["as_of", "symbol", "target", "horizon"]
    a = o[o.source == "debate"].drop_duplicates(k)
    b = o[o.source == "batch"].drop_duplicates(k)
    j = a.merge(b, on=k, suffixes=("_debate", "_batch"))
    if j.empty:
        return pd.DataFrame()
    return pd.DataFrame([dict(pairs=len(j), debate_brier=j.brier_debate.mean(), batch_brier=j.brier_batch.mean(),
                              base_brier=j.brier_base_batch.mean(), enough=len(j) >= MIN_CELL)])


def unavailable_share(forecasts):
    if forecasts is None or forecasts.empty:
        return None
    return float((forecasts.verdict == "Forecast unavailable").mean())

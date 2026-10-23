"""Ranking of qualified candidates. Transparent, point-in-time, and labelled HEURISTIC: no ranking is
called predictive unless a ranking experiment shows it (see screener/experiments.py)."""
import numpy as np, pandas as pd
from .setups import pctile_by_date

COMPONENT_SOURCE = {"rs_126": "rel_return_126", "rs_63": "rel_return_63", "rel_volume": "rel_volume_20d",
                    "adx": "adx14", "trend_slope": "sma50_slope_20d"}


def _src(f, col):
    if col.startswith("rel_return_") and f[col].isna().all():
        return col.replace("rel_return_", "return_") + "d"
    return col


def rank(f, qualified, n_setups, rcfg):
    """Scores only qualified rows; percentiles are among qualified rows on the same date.
    Returns DataFrame: ranking_score, rank, plus c_<component> columns (0-100)."""
    q = np.asarray(qualified, dtype=bool)
    out = pd.DataFrame(index=f.index)
    comps = {}
    for k, col in COMPONENT_SOURCE.items():
        comps[k] = pctile_by_date(f, _src(f, col), q)
    comps["setups"] = pd.Series(np.where(q, n_setups, np.nan), index=f.index).groupby(f.date).rank(pct=True).to_numpy() * 100
    m = rcfg["method"]
    if m == "relative_strength_126":
        score = comps["rs_126"]
    elif m == "relative_strength_63":
        score = comps["rs_63"]
    elif m == "setup_count_then_rs":
        score = np.where(q, n_setups * 1000 + np.nan_to_num(comps["rs_126"]), np.nan)
    else:
        w = {k: float(v) for k, v in rcfg.get("weights", {}).items() if v}
        tot = sum(w.values()) or 1.0
        score = sum(np.nan_to_num(comps[k], nan=0.0) * v for k, v in w.items()) / tot
        score = np.where(q, score, np.nan)
    for k in ("rs_126", "rs_63", "rel_volume", "adx", "trend_slope"):
        out[f"c_{k}"] = comps[k]
    out["ranking_score"] = score
    # rank 1 = best; ties broken by symbol so output is reproducible
    tmp = pd.DataFrame({"date": f.date, "s": score, "sym": f.symbol}).loc[q]
    tmp = tmp.sort_values(["date", "s", "sym"], ascending=[True, False, True])
    tmp["rank"] = tmp.groupby("date").cumcount() + 1
    out["rank"] = tmp["rank"].reindex(f.index)
    return out

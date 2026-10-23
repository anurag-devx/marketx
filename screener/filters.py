"""Hard eligibility filters: one pass/fail column per rule plus the reasons, never one opaque flag."""
import numpy as np, pandas as pd
from .universe import is_etf

FILTERS = {
    "f_universe": "not in the selected universe on this date",
    "f_min_close": "close below {min_close}",
    "f_min_history": "fewer than {min_history} sessions of history",
    "f_liquidity": "median traded value (previous 20 sessions) below ₹{min_median_value_cr} cr",
    "f_valid_obs": "fewer than {pct}% of the last 60 sessions traded",
    "f_not_etf": "ETF",
    "f_data_ok": "unreviewed data error",
}


SHORT = {"f_universe": "outside universe", "f_min_close": "price too low", "f_min_history": "too little history",
         "f_liquidity": "too illiquid", "f_valid_obs": "too many missing sessions", "f_not_etf": "ETF", "f_data_ok": "data error"}


def apply(feat, fcfg, in_universe, flagged=None):
    """feat: feature rows. in_universe: bool array. flagged: set of symbols with an open data error (or a
    boolean array per row). Adds one column per filter, `eligible`, and `rejection_reasons`."""
    f = feat
    names = f.get("company_name", pd.Series("", index=f.index))
    out = pd.DataFrame(index=f.index)
    out["f_universe"] = np.asarray(in_universe, dtype=bool)
    out["f_min_close"] = (f.close >= fcfg["min_close"]).to_numpy()
    out["f_min_history"] = (f.history_bars >= fcfg["min_history"]).to_numpy()
    out["f_liquidity"] = (f.median_value_20d_cr >= fcfg["min_median_value_cr"]).fillna(False).to_numpy()
    out["f_valid_obs"] = (f.valid_ratio_60d >= fcfg["min_valid_ratio_60d"]).fillna(False).to_numpy()
    if fcfg["exclude_etf"]:
        etf = {s: is_etf(s, n) for s, n in zip(f.symbol.unique(), names.groupby(f.symbol).first().reindex(f.symbol.unique()).fillna(""))}
        out["f_not_etf"] = ~f.symbol.map(etf).to_numpy(dtype=bool)
    else:
        out["f_not_etf"] = True
    if fcfg["exclude_data_errors"] and flagged is not None:
        out["f_data_ok"] = ~(np.asarray(flagged, dtype=bool) if not isinstance(flagged, (set, frozenset)) else f.symbol.isin(flagged).to_numpy())
    else:
        out["f_data_ok"] = True
    cols = list(FILTERS)
    out["eligible"] = out[cols].all(axis=1)
    msg = {k: v.format(pct=round(100 * fcfg["min_valid_ratio_60d"]), **fcfg) for k, v in FILTERS.items()}
    fails = ~out[cols].to_numpy()
    out["rejection_reasons"] = ["; ".join(msg[c] for c, x in zip(cols, row) if x) for row in fails]
    return out

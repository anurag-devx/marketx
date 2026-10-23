"""Strategy agent research #1: medium-term momentum with monthly rebalancing.

Each month-end, rank liquid stocks by their past L-month return (skipping the most recent S months),
buy the top N at the next month's first open, hold H months (H>1 = overlapping cohorts), pay 0.3% per
name replaced. Optional regime filter: hold cash when Nifty is below its 200-day average.

Compared with an equal-weight portfolio of every liquid stock (the control) in the same months.
Discipline: variants are searched on the design period only; the chosen variant is then checked once on
validation (2021-23) and reported on holdout (2024+), which is never used to choose."""
import itertools
from statistics import NormalDist
import numpy as np, pandas as pd
from core import config as C

LOOKBACKS, SKIPS, HOLDS, FILTERS = (3, 6, 12), (0, 1), (1, 3), (False, True)
TOP_N = 20


def panel(px, idx):
    px = px.sort_values(["symbol", "date"]).copy()
    px["turnover_cr"] = (px.close * px.volume).groupby(px.symbol).transform(lambda s: s.rolling(20).mean()) / 1e7
    px["month"] = px.date.dt.to_period("M")
    g = px.groupby(["symbol", "month"])
    m = g.agg(close=("close", "last"), open_first=("open", "first"), turnover=("turnover_cr", "last")).reset_index()
    wide = {k: m.pivot(index="month", columns="symbol", values=k).sort_index() for k in ("close", "open_first", "turnover")}
    ix = idx.sort_values("date").copy()
    ix["sma200"] = ix.nifty.rolling(200).mean()
    ix["month"] = ix.date.dt.to_period("M")
    nm = ix.groupby("month").agg(nifty=("nifty", "last"), sma200=("sma200", "last"))
    return wide, nm.reindex(wide["close"].index)


def run_variant(wide, nifty_m, L, S, H, regime_filter, n=TOP_N, cost=C.COST_RT, want_picks=False):
    close, opn, turn = wide["close"], wide["open_first"], wide["turnover"]
    fwd = opn.shift(-1) / opn - 1                        # month k: first open of k -> first open of k+1
    mom = close.shift(1 + S) / close.shift(1 + S + L) - 1  # known at end of month k-1
    elig = (turn.shift(1) >= C.MIN_TURNOVER_CR) & (close.shift(1) >= C.MIN_PRICE) & mom.notna() & fwd.notna()
    risk_on = (nifty_m.nifty > nifty_m.sma200).astype(bool).shift(1, fill_value=False)
    picks = {}
    for k in close.index:
        e = elig.loc[k]
        if e.sum() < n * 3:
            continue
        picks[k] = set(mom.loc[k][e].nlargest(n).index)
    months = sorted(picks)
    rows, prev_in = [], False
    for i, k in enumerate(months):
        ctrl = fwd.loc[k][elig.loc[k]].mean()
        cohorts = [picks[months[j]] for j in range(max(0, i - H + 1), i + 1)]
        on = bool(risk_on.loc[k]) if regime_filter else True
        if on:
            gross = np.mean([fwd.loc[k][list(c)].mean() for c in cohorts])
            new = picks[k]
            old = picks[months[i - H]] if i - H >= 0 else set()
            replaced = len(new - old) / n
            c = replaced * cost / H if prev_in else cost / 2      # entering from cash: buy side only
        else:
            gross, c = 0.0, (cost / 2 if prev_in else 0.0)       # going to cash: sell side
        rows.append(dict(month=str(k), gross=gross, net=gross - c, control=ctrl, invested=on))
        prev_in = on
    r = pd.DataFrame(rows)
    r["excess"] = r.net - r.control
    if want_picks:
        pk = pd.DataFrame([dict(month=str(k), symbol=sym) for k in months for sym in picks[k]])
        return r, pk
    return r


def period_of(month):
    d = pd.Period(month).to_timestamp()
    if d < pd.Timestamp(C.DESIGN[0]):
        return "warmup"
    if d <= pd.Timestamp(C.DESIGN[1]):
        return "design"
    if d <= pd.Timestamp(C.VALIDATION[1]):
        return "validation"
    return "holdout"


def stats(r):
    if len(r) < 6:
        return dict(months=len(r))
    eq = (1 + r.net).cumprod()
    yrs = len(r) / 12
    ex = r.excess
    return dict(months=len(r), cagr_pct=100 * (eq.iat[-1] ** (1 / yrs) - 1),
                control_cagr_pct=100 * ((1 + r.control).prod() ** (1 / yrs) - 1),
                avg_month_net_pct=100 * r.net.mean(), avg_month_excess_pct=100 * ex.mean(),
                t_excess=ex.mean() / (ex.std() / np.sqrt(len(ex))) if ex.std() > 0 else np.nan,
                max_dd_pct=100 * (eq / eq.cummax() - 1).min(),
                sharpe=np.sqrt(12) * r.net.mean() / r.net.std() if r.net.std() > 0 else np.nan,
                pct_months_invested=100 * r.invested.mean())


def study(px, idx):
    wide, nm = panel(px, idx)
    variants, monthly = [], {}
    for L, S, H, F in itertools.product(LOOKBACKS, SKIPS, HOLDS, FILTERS):
        vid = f"MOM_L{L}_S{S}_H{H}{'_F' if F else ''}"
        r = run_variant(wide, nm, L, S, H, F)
        r["period"] = r.month.map(period_of)
        r = r[r.period != "warmup"]
        monthly[vid] = r
        row = dict(variant=vid, lookback_m=L, skip_m=S, hold_m=H, regime_filter=F)
        for p in ("design", "validation", "holdout"):
            for k, v in stats(r[r.period == p]).items():
                row[f"{p}_{k}"] = v
        for k, v in stats(r).items():
            row[f"all_{k}"] = v
        variants.append(row)
    v = pd.DataFrame(variants)
    n = len(v)
    t_search = NormalDist().inv_cdf(1 - 0.05 / (2 * n))      # Bonferroni threshold for n tries, shown for reference
    best = v.sort_values("design_t_excess", ascending=False).iloc[0]
    verdict = dict(
        variants_tested=n, chosen=best.variant, design_t=best.design_t_excess, search_adjusted_t=t_search,
        design_pass=bool(best.design_t_excess >= 2.0),
        validation_pass=bool(best.validation_avg_month_excess_pct > 0 and best.validation_t_excess >= 1.65),
        holdout_positive=bool(best.holdout_avg_month_excess_pct > 0))
    verdict["passes"] = verdict["design_pass"] and verdict["validation_pass"] and verdict["holdout_positive"]
    return v, monthly, verdict

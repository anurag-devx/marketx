"""Historical evaluation of screener signals. These are STOCK-LEVEL SIGNAL STATISTICS, not portfolio returns.

For a signal at the close of session t (bars of that stock):
  entry  = open of the stock's next bar (t+1)
  exit   = close of the stock's bar t+h, for h in the horizons (5, 10, 15, 20)
  gross  = exit / entry - 1;  net = gross - round-trip cost
  benchmark-relative = gross - Nifty(close at the exit date) / Nifty(close at t) - 1
      (index opens are not in the data, so the index leg runs close t -> close t+h; documented approximation)
A label is dropped if the stock's bar t+h is more than h + 5 trading sessions after t (a suspension or gap).

Baseline: every ELIGIBLE stock on the same date with the same labels (same universe, dates, costs).
Signal vs baseline is matched by date: each signal is compared with that date's eligible-stock average.

Periods: development 2016-20, validation 2021-23, holdout 2024+. A signal belongs to the period of its
date t and is PURGED if its label ends after that period's last day (no label crosses a boundary).

Uncertainty: signals on the same day and overlapping holding windows are correlated, so statistics are
computed on the daily series of (signal average - baseline average) and their standard error uses
Newey-West with h lags. 95% intervals = mean +/- 1.96 x SE."""
import math
import numpy as np, pandas as pd
from core import config as C

EVAL_VERSION = "1.0"
PERIODS = [("development", C.DESIGN[0], C.DESIGN[1]), ("validation", C.VALIDATION[0], C.VALIDATION[1]),
           ("holdout", C.HOLDOUT_FROM, "2100-12-31")]
PERIOD_LABEL = {"development": "Development 2016–20", "validation": "Validation 2021–23", "holdout": "Holdout 2024+"}

CRITERIA = dict(dev_min_signals=300, dev_min_dates=50, dev_min_t=2.0, val_min_signals=100, val_min_dates=30, val_min_t=1.65,
                max_p5_gap_pp=2.0, min_positive_year_share=0.6)


# ---------------- labels ----------------
def labels(res, horizons, bench, calendar):
    """Adds gross_<h>, bench_<h>, end_<h> (label end date) to every row. Uses only future bars as LABELS."""
    out = {}
    g = res.groupby("symbol", sort=False)
    nxt_open = g.open.shift(-1)
    cal = pd.DatetimeIndex(calendar)
    pos = pd.Series(np.searchsorted(cal.to_numpy(), res.date.to_numpy()), index=res.index)
    nm = bench.dropna(subset=["nifty"]).set_index("date").nifty
    n_t = res.date.map(nm)
    for h in horizons:
        exit_close = g.close.shift(-h)
        end = g.date.shift(-h)
        end_pos = pd.Series(np.searchsorted(cal.to_numpy(), end.fillna(pd.Timestamp("2200-01-01")).to_numpy()), index=res.index)
        ok = end.notna() & nxt_open.notna() & ((end_pos - pos) <= h + 5)
        out[f"gross_{h}"] = (exit_close / nxt_open - 1).where(ok)
        out[f"bench_{h}"] = (end.map(nm) / n_t - 1).where(ok)
        out[f"end_{h}"] = end.where(ok)
    return pd.concat([res, pd.DataFrame(out, index=res.index)], axis=1)


def period_of(dates):
    d = pd.to_datetime(pd.Series(dates))
    p = pd.Series(None, index=d.index, dtype=object)
    for name, a, b in PERIODS:
        p[(d >= a) & (d <= b)] = name
    return p


def purge_mask(dates, ends):
    """True where the label ends inside the same period as its signal date."""
    p = period_of(dates).to_numpy()
    ok = np.zeros(len(p), dtype=bool)
    ends = pd.to_datetime(pd.Series(ends)).to_numpy()
    for name, a, b in PERIODS:
        m = p == name
        ok |= m & (ends <= np.datetime64(pd.Timestamp(b)))
    return ok & ~pd.isna(ends)


# ---------------- statistics ----------------
def newey_west_se(x, lags):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return np.nan
    e = x - x.mean()
    s = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        w = 1 - k / (lags + 1)
        s += 2 * w * (e[k:] @ e[:-k]) / n
    return math.sqrt(max(s, 0) / n)


def _summ(sig, base_day, h, cost):
    """sig: signal rows (with gross_h, bench_h, date). base_day: per-date baseline (mean, pos, p5)."""
    if sig.empty:
        return dict(signals=0)
    gr = sig[f"gross_{h}"]
    net = gr - cost
    b = base_day.reindex(sig.date)
    b_mean, b_pos = b["mean"].to_numpy(), b["pos"].to_numpy()
    day = pd.DataFrame({"date": sig.date.to_numpy(), "g": gr.to_numpy(), "pos": (gr > 0).to_numpy(float),
                        "bm": b_mean, "bp": b_pos}).groupby("date").mean()
    ex_d = (day.g - day.bm).to_numpy()
    lift_d = (day.pos - day.bp).to_numpy()
    se_ex, se_lift = newey_west_se(ex_d, h), newey_west_se(lift_d, h)
    ex = float(np.nanmean(ex_d))
    lift = float(np.nanmean(lift_d))
    years = sig.date.dt.year
    by_year = pd.Series(gr.to_numpy() - b_mean, index=years.to_numpy()).groupby(level=0).mean()
    return dict(
        signals=int(len(sig)), stocks=int(sig.symbol.nunique()), dates=int(sig.date.nunique()),
        positive_rate=100 * float((gr > 0).mean()), baseline_positive_rate=100 * float(np.nanmean(b_pos)),
        lift_pp=100 * lift, lift_ci95=[100 * (lift - 1.96 * se_lift), 100 * (lift + 1.96 * se_lift)],
        mean_net=100 * float(net.mean()), median_net=100 * float(net.median()), mean_gross=100 * float(gr.mean()),
        baseline_mean_gross=100 * float(np.nanmean(b_mean)),
        excess=100 * ex, excess_ci95=[100 * (ex - 1.96 * se_ex), 100 * (ex + 1.96 * se_ex)],
        t_excess=ex / se_ex if se_ex and se_ex > 0 else np.nan,
        p5_net=100 * float(net.quantile(0.05)), p25_net=100 * float(net.quantile(0.25)),
        baseline_p5_net=100 * float(np.nanmean(b["p5"].to_numpy())) - 100 * cost,
        bench_relative=100 * float((gr - sig[f"bench_{h}"]).mean()),
        positive_year_share=float((by_year > 0).mean()) if len(by_year) else np.nan,
        warning=("small sample" if len(sig) < 100 or sig.date.nunique() < 30 else ""))


def evaluate(res, horizons, bench, calendar, cost_pct, select="qualifies", top_n=None, regime=None):
    """res: compute() output for all dates. select: 'qualifies' (all qualifying) or 'shortlist' (rank <= top_n).
    Returns dict: by_period[period][h], by_year / by_regime / by_liquidity tables (development+validation+holdout,
    the holdout is filtered out by the UI until revealed), coverage, warnings."""
    cost = float(cost_pct) / 100
    lab = labels(res, horizons, bench, calendar)
    elig = lab[lab.eligible]
    sig_mask = lab.qualifies & lab.eligible
    if select == "shortlist" and top_n:
        sig_mask &= lab["rank"] <= int(top_n)
    out = dict(by_period={}, by_year=[], by_regime=[], by_liquidity=[], coverage={}, horizons=list(horizons),
               select=select, eval_version=EVAL_VERSION)
    # liquidity tercile per date among eligible rows (point in time)
    liq = elig.groupby("date").median_value_20d_cr.rank(pct=True)
    lab["liq_tercile"] = pd.cut(liq.reindex(lab.index), [0, 1 / 3, 2 / 3, 1.0001], labels=["low", "mid", "high"])
    if regime is not None:
        lab["regime"] = lab.date.map(regime).fillna("unknown")
    for h in horizons:
        keep = purge_mask(lab.date, lab[f"end_{h}"]) & lab[f"gross_{h}"].notna()
        e = lab[keep & lab.eligible]
        base_day = e.groupby("date")[f"gross_{h}"].agg(mean="mean", pos=lambda x: (x > 0).mean(), p5=lambda x: x.quantile(0.05))
        s = lab[keep & sig_mask]
        per = period_of(s.date)
        for p, _, _ in PERIODS:
            out["by_period"].setdefault(p, {})[h] = _summ(s[per.to_numpy() == p], base_day, h, cost)
        for p, _, _ in PERIODS:
            sp = s[per.to_numpy() == p]
            for col, key in (("year", "by_year"), ("regime", "by_regime"), ("liq_tercile", "by_liquidity")):
                if col != "year" and col not in sp:
                    continue
                grp = sp.date.dt.year.to_numpy() if col == "year" else sp[col]
                for k, g in sp.groupby(grp, observed=True):
                    r = _summ(g, base_day, h, cost)
                    out[key].append(dict(group=str(k), horizon=h, period=p, **{x: r.get(x) for x in
                                         ("signals", "positive_rate", "baseline_positive_rate", "lift_pp", "mean_net", "excess", "t_excess")}))
        out["coverage"][h] = dict(signals_before_purge=int((sig_mask & lab[f"gross_{h}"].notna()).sum()), signals_after_purge=int(len(s)),
                                  first=str(s.date.min().date()) if len(s) else None, last=str(s.date.max().date()) if len(s) else None)
    return out


# ---------------- validation criteria ----------------
def validate(ev, horizon, configs_tested=1):
    """Is this a validated research candidate at `horizon`? Uses development and validation only.
    Returns dict(validated, checks[], search_t)."""
    from statistics import NormalDist
    c = CRITERIA
    d = ev["by_period"].get("development", {}).get(horizon, {}) or {}
    v = ev["by_period"].get("validation", {}).get(horizon, {}) or {}
    yrs = [r for r in ev["by_year"] if r["horizon"] == horizon and r["period"] in ("development", "validation") and r["signals"]]
    pos_share = np.mean([r["excess"] > 0 for r in yrs]) if yrs else np.nan
    g = lambda x, k, default=np.nan: x.get(k, default) if x else default
    checks = [
        ("development", "enough signals and dates", g(d, "signals", 0) >= c["dev_min_signals"] and g(d, "dates", 0) >= c["dev_min_dates"],
         f"{g(d, 'signals', 0)} signals on {g(d, 'dates', 0)} dates (need {c['dev_min_signals']} / {c['dev_min_dates']})"),
        ("development", "beats the eligible baseline", g(d, "excess", -1) > 0 and g(d, "t_excess", 0) >= c["dev_min_t"],
         f"{g(d, 'excess'):+.2f}% vs baseline, t {g(d, 't_excess'):.1f} (need {c['dev_min_t']})"),
        ("development", "positive after costs", g(d, "mean_net", -1) > 0, f"{g(d, 'mean_net'):+.2f}% net"),
        ("validation", "enough signals and dates", g(v, "signals", 0) >= c["val_min_signals"] and g(v, "dates", 0) >= c["val_min_dates"],
         f"{g(v, 'signals', 0)} signals on {g(v, 'dates', 0)} dates (need {c['val_min_signals']} / {c['val_min_dates']})"),
        ("validation", "beats the eligible baseline", g(v, "excess", -1) > 0 and g(v, "t_excess", 0) >= c["val_min_t"],
         f"{g(v, 'excess'):+.2f}% vs baseline, t {g(v, 't_excess'):.1f} (need {c['val_min_t']})"),
        ("validation", "positive after costs", g(v, "mean_net", -1) > 0, f"{g(v, 'mean_net'):+.2f}% net"),
        ("both", "downside no worse than the baseline", all(g(x, "p5_net", -99) >= g(x, "baseline_p5_net", 0) - c["max_p5_gap_pp"] for x in (d, v)),
         f"5% worst: {g(d, 'p5_net'):+.1f}% / {g(v, 'p5_net'):+.1f}% vs baseline {g(d, 'baseline_p5_net'):+.1f}% / {g(v, 'baseline_p5_net'):+.1f}%"),
        ("both", "stable across years", pos_share == pos_share and pos_share >= c["min_positive_year_share"],
         f"beats the baseline in {0 if pos_share != pos_share else round(100 * pos_share)}% of years (need {round(100 * c['min_positive_year_share'])}%)"),
    ]
    st = NormalDist().inv_cdf(1 - 0.05 / (2 * max(1, configs_tested)))
    return dict(validated=all(x[2] for x in checks), horizon=horizon, search_t=st, search_ok=bool(g(d, "t_excess", 0) >= st),
                configs_tested=int(configs_tested),
                checks=[dict(period=a, test=b, ok=bool(ok), detail=det) for a, b, ok, det in checks])

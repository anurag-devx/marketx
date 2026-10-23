"""Statistical validation agent: tests claims on history with code, never with a language model.

TECHNICAL SIGNALS (debate/signals.py), stock-specific signals: same-day cross-sectional test.
  For every trading day t where the signal fires for some stocks, d_t = mean outcome of firing stocks - mean outcome of
  the other eligible stocks that day. The same-day control removes the market's move, so d_t isolates the signal.
  Forward windows of h sessions overlap; days are grouped into non-overlapping blocks of h sessions, the block means
  are tested with a Newey-West (lag 1) t-statistic and a block bootstrap 95% interval.
MARKET-WIDE SIGNALS (Nifty trend, breadth, VIX): the same for every stock on a day, so they are tested over time:
  every h-th day, outcome = the average stock's outcome; signal days vs other days (Welch t, bootstrap interval).
STOCK ONLY (supplementary): this stock's own firing days, declustered (one per h sessions) vs its other days.
EVENTS (news / NSE filings by category): event study. Event day = first session the market could react
  (published <= 15:30 IST on a trading day -> that day, else the next session). Abnormal return = stock - Nifty
  ('market_adjusted') or stock - beta x Nifty with beta from sessions -250..-30 ('market_model').
  CAR(1, h) = the drift over the h sessions AFTER the event day (the tradable part). Events of the same stock and
  category within 3 sessions count once; averages are taken per event day (events on one day move together).
MACRO: the weekly sensitivity regressions from research/macro_impact.py (association only, in-sample).

Chronology: design 2016-20, validation 2021-23 (config), recent 2024+ reported only. Events (short history):
first half vs second half of the event dates. Only observations whose outcome window ENDED on or before the analysis
cutoff are used, so nothing after the cutoff leaks in. Multiple testing: Benjamini-Hochberg across all tests of one
analysis on the design-period p-values.

Statuses: Supported by historical evidence / Partially supported / Not supported / Contradicted by evidence /
Insufficient data / Qualitative only / Invalid test specification.
'Not supported' is not 'false': it means the data could not show the effect (too small, too noisy or absent)."""
import numpy as np, pandas as pd
from scipy import stats as sps
from core import config as C
from . import signals as SG

MIN_EFF_N = 30          # non-overlapping observations needed in each of design and validation
MIN_EVENTS = 30         # events per half (event study)
MIN_EVENT_DAYS = 20     # distinct event days per half
MIN_STOCK_N = 15        # declustered firing days for the stock-only test
FDR_Q = 0.10
VAL_P = 0.10            # one-sided p in validation needed to confirm a design result
N_BOOT = 1000

SUPPORTED, PARTIAL, NOT_SUPPORTED, CONTRA, INSUFF, QUAL, INVALID = (
    "Supported by historical evidence", "Partially supported", "Not supported", "Contradicted by evidence",
    "Insufficient data", "Qualitative only", "Invalid test specification")
STATUS_ORDER = [SUPPORTED, PARTIAL, NOT_SUPPORTED, CONTRA, INSUFF, QUAL, INVALID]


# ---------------- primitives ----------------
def nw_mean(x, lag=1):
    """Mean, Newey-West standard error, t and two-sided p for a series that may be autocorrelated."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 3:
        return dict(n=n, mean=float(np.mean(x)) if n else np.nan, se=np.nan, t=np.nan, p=np.nan)
    m = x.mean()
    e = x - m
    v = e @ e / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1 - k / (lag + 1)
        v += 2 * w * (e[k:] @ e[:-k]) / n
    se = np.sqrt(max(v, 0) / n)
    t = m / se if se > 0 else np.nan
    p = float(2 * sps.t.sf(abs(t), n - 1)) if np.isfinite(t) else np.nan
    return dict(n=n, mean=float(m), se=float(se), t=float(t) if np.isfinite(t) else np.nan, p=p)


def bootstrap_ci(x, block=1, n_boot=N_BOOT, seed=11, alpha=0.05):
    """Moving-block bootstrap interval for the mean (block=1 -> ordinary bootstrap)."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    b = max(1, min(block, n))
    nb = int(np.ceil(n / b))
    starts = rng.integers(0, n - b + 1, size=(n_boot, nb))
    idx = (starts[:, :, None] + np.arange(b)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = x[idx].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def bootstrap_diff_ci(a, b, n_boot=N_BOOT, seed=11):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return (np.nan, np.nan)
    rng = np.random.default_rng(seed)
    d = rng.choice(a, (n_boot, len(a))).mean(1) - rng.choice(b, (n_boot, len(b))).mean(1)
    return (float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975)))


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return dict(t=np.nan, p=np.nan)
    r = sps.ttest_ind(a, b, equal_var=False)
    return dict(t=float(r.statistic), p=float(r.pvalue))


def mann_whitney(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return dict(u=np.nan, p=np.nan)
    r = sps.mannwhitneyu(a, b, alternative="two-sided")
    return dict(u=float(r.statistic), p=float(r.pvalue))


def prop_test(k1, n1, k2, n2):
    """Two-proportion z-test (pooled)."""
    if min(n1, n2) == 0:
        return dict(z=np.nan, p=np.nan)
    p1, p2, pp = k1 / n1, k2 / n2, (k1 + k2) / (n1 + n2)
    se = np.sqrt(pp * (1 - pp) * (1 / n1 + 1 / n2))
    if se == 0:
        return dict(z=np.nan, p=np.nan)
    z = (p1 - p2) / se
    return dict(z=float(z), p=float(2 * sps.norm.sf(abs(z))))


def bh(pvals):
    """Benjamini-Hochberg adjusted p-values (NaN stays NaN)."""
    p = np.asarray(pvals, float)
    out = np.full(len(p), np.nan)
    ok = ~np.isnan(p)
    if not ok.any():
        return out
    q = p[ok]
    order = np.argsort(q)
    m = len(q)
    adj = q[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    res = np.empty(m)
    res[order] = np.minimum(adj, 1)
    out[ok] = res
    return out


def one_sided(p, effect, sign):
    """Two-sided p -> one-sided p for the claimed direction."""
    if not np.isfinite(p) or not np.isfinite(effect):
        return np.nan
    return p / 2 if effect * sign > 0 else 1 - p / 2


# ---------------- outcome definitions ----------------
def outcome(p, target, band=None):
    """(y in %, hit) for a frame with fwd / fwd_rel. target: abs | rel | three."""
    y = p.fwd_rel if target == "rel" else p.fwd
    thr = (band or 0) / 100 if target == "three" else 0.0
    return 100 * y, (y > thr).where(y.notna())


def periods(cutoff):
    d0, d1 = pd.Timestamp(C.DESIGN[0]), pd.Timestamp(C.DESIGN[1])
    v0, v1 = pd.Timestamp(C.VALIDATION[0]), pd.Timestamp(C.VALIDATION[1])
    return {"design": (d0, d1), "validation": (v0, v1), "recent": (pd.Timestamp(C.HOLDOUT_FROM), pd.Timestamp(cutoff))}


def eligible(p):
    """Liquid, priced, enough history: the comparison universe."""
    return (p.turnover_cr >= C.MIN_TURNOVER_CR) & (p.close >= C.MIN_PRICE) & (p.n_hist >= 252)


# ---------------- signal tests ----------------
def _cs_period(dcode, y, hit, fire, elig, h):
    """Same-day cross-sectional spread inside one period. Arrays aligned; dcode = trading-day index."""
    m = elig & ~np.isnan(y)
    if not m.any():
        return None
    dc, yy, hh, ff = dcode[m], y[m], hit[m], fire[m]
    D = dc.max() + 1
    n_all = np.bincount(dc, minlength=D)
    s_all = np.bincount(dc, yy, minlength=D)
    h_all = np.bincount(dc, hh, minlength=D)
    n_f = np.bincount(dc, ff.astype(float), minlength=D)
    s_f = np.bincount(dc, yy * ff, minlength=D)
    h_f = np.bincount(dc, hh * ff, minlength=D)
    n_c = n_all - n_f
    ok = (n_f >= 1) & (n_c >= 20)
    if ok.sum() == 0:
        return dict(n_obs=0, n_days=0, eff_n=0)
    days = np.nonzero(ok)[0]
    spread = s_f[ok] / n_f[ok] - (s_all[ok] - s_f[ok]) / n_c[ok]
    blocks = days // h
    bdf = pd.DataFrame({"b": blocks, "s": spread}).groupby("b").s.mean()
    t = nw_mean(bdf.to_numpy(), lag=1)
    ci = bootstrap_ci(bdf.to_numpy(), block=2)
    k1, n1 = h_f[ok].sum(), n_f[ok].sum()
    k2, n2 = (h_all[ok] - h_f[ok]).sum(), n_c[ok].sum()
    mf = s_f[ok].sum() / n1
    return dict(n_obs=int(n1), n_days=int(len(days)), eff_n=int(len(bdf)), effect=t["mean"], ci_lo=ci[0], ci_hi=ci[1],
                t=t["t"], p=t["p"], hit_signal=float(k1 / n1), hit_control=float(k2 / n2) if n2 else np.nan,
                mean_signal=float(mf), hit_p=prop_test(k1, n1, k2, n2)["p"])


def _ts_period(dates, ybar, hbar, fire_day, h):
    """Market-wide signal: every h-th day, average stock outcome, signal days vs other days."""
    sel = np.arange(0, len(dates), h)
    y, f, hr = ybar[sel], fire_day[sel], hbar[sel]
    ok = ~np.isnan(y)
    y, f, hr = y[ok], f[ok], hr[ok]
    a, b = y[f], y[~f]
    if len(a) == 0:
        return dict(n_obs=0, n_days=0, eff_n=0)
    w = welch(a, b)
    ci = bootstrap_diff_ci(a, b)
    eff = float(a.mean() - b.mean()) if len(b) else np.nan
    return dict(n_obs=int(len(a)), n_days=int(len(a)), eff_n=int(min(len(a), len(b))), effect=eff, ci_lo=ci[0], ci_hi=ci[1],
                t=w["t"], p=w["p"], hit_signal=float(np.nanmean(hr[f])), hit_control=float(np.nanmean(hr[~f])) if (~f).any() else np.nan,
                mean_signal=float(a.mean()), hit_p=np.nan)


def signal_tests(panel, sig, cutoff, h, target="abs", band=None, ids=None, regime_mask=None):
    """Tests every signal (or `ids`) on the whole liquid universe. panel must have fwd/fwd_rel/end_date (features.forward).
    Returns {signal_id: result dict}. Only rows whose outcome window ended on or before the cutoff."""
    cutoff = pd.Timestamp(cutoff)
    known = panel.end_date.notna() & (panel.end_date <= cutoff)
    y_all, hit_all = outcome(panel, target, band)
    y = np.where(known, y_all, np.nan)
    hit = np.where(known, hit_all.astype(float), np.nan)
    elig = eligible(panel).to_numpy()
    dates = panel.date
    uniq = np.sort(dates.unique())
    dcode = np.searchsorted(uniq, dates.to_numpy())
    P = periods(cutoff)
    out = {}
    for k in ids or SG.BY_ID:
        fire = sig[k].to_numpy()
        res = {}
        if k in SG.MARKET_WIDE:
            frame = pd.DataFrame({"d": dcode, "y": np.where(elig, y, np.nan), "h": np.where(elig, hit, np.nan), "f": fire})
            g = frame.groupby("d").agg(y=("y", "mean"), h=("h", "mean"), f=("f", "max"))
            gd = uniq[g.index.to_numpy()]
            for name, (a, b) in P.items():
                m = (gd >= np.datetime64(a)) & (gd <= np.datetime64(b))
                res[name] = _ts_period(gd[m], g.y.to_numpy()[m], g.h.to_numpy()[m], g.f.to_numpy()[m].astype(bool), h)
            method = "Time-series comparison: every h-th day, average stock outcome on signal days vs other days; Welch t-test; bootstrap 95% interval"
        else:
            for name, (a, b) in P.items():
                m = ((dates >= a) & (dates <= b)).to_numpy()
                r = _cs_period(dcode[m] - (dcode[m].min() if m.any() else 0), y[m], np.nan_to_num(hit[m]), fire[m], elig[m], h) if m.any() else None
                res[name] = r or dict(n_obs=0, n_days=0, eff_n=0)
            if regime_mask is not None:
                m = regime_mask.to_numpy() & (dates <= P["validation"][1]).to_numpy()
                r = _cs_period(dcode[m] - (dcode[m].min() if m.any() else 0), y[m], np.nan_to_num(hit[m]), fire[m], elig[m], h) if m.any() else None
                res["same_regime"] = r or dict(n_obs=0, n_days=0, eff_n=0)
            method = ("Same-day cross-sectional spread (firing stocks minus other liquid stocks), non-overlapping h-session blocks, "
                      "Newey-West t (lag 1), block bootstrap 95% interval; hit rates by two-proportion z-test")
        out[k] = dict(res, method=method)
    return out


def stock_only_test(panel_sym, fire, cutoff, h, target="abs", band=None):
    """This stock alone: declustered firing days vs its other days (every h-th)."""
    p = panel_sym.reset_index(drop=True)
    known = (p.end_date.notna() & (p.end_date <= pd.Timestamp(cutoff))).to_numpy()
    y, _ = outcome(p, target, band)
    y = np.where(known, y, np.nan)
    f = np.asarray(fire, bool)
    picks, last = [], -10 ** 9
    for i in np.nonzero(f & ~np.isnan(y))[0]:
        if i - last >= h:
            picks.append(i); last = i
    ctrl = [i for i in range(0, len(p), h) if not f[i] and not np.isnan(y[i])]
    a, b = y[picks], y[ctrl]
    w, mw = welch(a, b), mann_whitney(a, b)
    ci = bootstrap_diff_ci(a, b)
    return dict(n_signal=len(a), n_control=len(b), effect=float(np.mean(a) - np.mean(b)) if len(a) and len(b) else np.nan,
                ci_lo=ci[0], ci_hi=ci[1], t=w["t"], p=w["p"], mw_p=mw["p"],
                hit_signal=float(np.mean(a > ((band or 0) if target == "three" else 0))) if len(a) else np.nan,
                enough=len(a) >= MIN_STOCK_N)


# ---------------- classification ----------------
def classify(res, side, q_design=None):
    """Status for a claim from its design/validation results. side: bull (+) or bear (-)."""
    sign = 1 if side == "bull" else -1
    d, v = res.get("design") or {}, res.get("validation") or {}
    if min(d.get("eff_n", 0), v.get("eff_n", 0)) < MIN_EFF_N or not np.isfinite(d.get("effect", np.nan)) or not np.isfinite(v.get("effect", np.nan)):
        return INSUFF, f"needs {MIN_EFF_N}+ non-overlapping observations in both design and validation (has {d.get('eff_n', 0)} and {v.get('eff_n', 0)})"
    qd = q_design if q_design is not None and np.isfinite(q_design) else d.get("p", np.nan)
    sd, sv = np.sign(d["effect"]) * sign, np.sign(v["effect"]) * sign
    pv1 = one_sided(v.get("p", np.nan), v["effect"], sign)
    sig_d = np.isfinite(qd) and qd < FDR_Q
    if sig_d and sd > 0 and sv > 0 and np.isfinite(pv1) and pv1 < VAL_P:
        return SUPPORTED, "significant in design (after multiple-testing correction) and confirmed in validation"
    if sig_d and sd < 0 and sv < 0:
        return CONTRA, "significant in the opposite direction in design, and opposite again in validation"
    if sd > 0 and sv > 0:
        return PARTIAL, "same direction as claimed in design and validation, but not strong enough to confirm"
    if sig_d and sd > 0:
        return PARTIAL, "significant in design but not repeated in validation"
    return NOT_SUPPORTED, "no consistent effect in the claimed direction"


def economic(res, h):
    """Is the effect large enough to matter after costs? Uses the validation-period effect."""
    v = (res.get("validation") or {}).get("effect", np.nan)
    cost = 100 * C.COST_RT
    if not np.isfinite(v):
        return "unknown"
    return (f"{abs(v):.2f} pp over {h} sessions vs {cost:.2f}% round-trip cost: "
            + ("larger than costs" if abs(v) > cost else "smaller than costs (not tradable on its own)"))


def finalize(tests, sides):
    """Adds q-values (BH across all tests) and statuses. tests: {id: res}; sides: {id: side}."""
    ids = list(tests)
    q = bh([tests[k].get("design", {}).get("p", np.nan) for k in ids])
    for k, qq in zip(ids, q):
        tests[k]["q_design"] = float(qq) if np.isfinite(qq) else np.nan
        st, why = classify(tests[k], sides[k], qq)
        tests[k]["status"], tests[k]["why"] = st, why
    return tests


# ---------------- event study ----------------
def _ar_panel(px, idx):
    from research.events import returns_panel
    return returns_panel(px, idx)


def event_sample(news, ann, P, cutoff, h, dedupe_sessions=3):
    """All historical events with event day, CAR(1,h) known by the cutoff. One row per (symbol, event)."""
    from research.events import events_from, event_day
    ev = events_from(news, ann)
    if ev.empty:
        return ev
    dates = P["dates"]
    ev = ev[ev.symbol.isin(P["ar"].columns)].copy()
    ev["d0"] = [event_day(t, dates) for t in ev.published]
    ev = ev[ev.d0.notna()].copy()
    ev["pos"] = dates.get_indexer(ev.d0)
    ci = dates.get_indexer([pd.Timestamp(cutoff)])[0]
    if ci < 0:
        ci = dates.searchsorted(pd.Timestamp(cutoff), side="right") - 1
    ev = ev[ev.pos + h <= ci].sort_values(["symbol", "source_type", "category", "pos"])
    keep, last = [], {}
    for r in ev.itertuples():
        key = (r.symbol, r.source_type, r.category)
        if key in last and r.pos - last[key] <= dedupe_sessions:
            continue
        last[key] = r.pos
        keep.append(r.Index)
    return ev.loc[keep]


def car(ev, P, h, model="market_adjusted"):
    """CAR(1,h) and AR(0) in % per event."""
    ret, nr = P["ret"], P["nifty"].to_numpy()
    R = ret.to_numpy()
    cols = ret.columns.get_indexer(ev.symbol)
    T = len(P["dates"])
    car_, ar0 = np.full(len(ev), np.nan), np.full(len(ev), np.nan)
    for k, (p, c) in enumerate(zip(ev.pos.to_numpy(), cols)):
        if c < 0 or p + h >= T:
            continue
        beta = 1.0
        if model == "market_model" and p - 250 >= 0:
            x, y = nr[p - 250:p - 30], R[p - 250:p - 30, c]
            ok = ~np.isnan(x) & ~np.isnan(y)
            if ok.sum() >= 100 and np.var(x[ok]) > 0:
                beta = float(np.cov(x[ok], y[ok])[0, 1] / np.var(x[ok], ddof=1))
        w = R[p + 1:p + h + 1, c] - beta * nr[p + 1:p + h + 1]
        if not np.isnan(w).any():
            car_[k] = 100 * w.sum()
        a0 = R[p, c] - beta * nr[p]
        ar0[k] = 100 * a0 if np.isfinite(a0) else np.nan
    return car_, ar0


def event_test(ev_cat, side, h):
    """ev_cat: events of one kind with car column. First half vs second half by event date."""
    if len(ev_cat) == 0:
        return dict(status=INSUFF, why="no historical events of this kind", n_events=0)
    e = ev_cat.dropna(subset=["car"]).sort_values("d0")
    days = np.sort(e.d0.unique())
    if len(days) < 2:
        return dict(status=INSUFF, why="too few event days", n_events=len(e))
    mid = days[len(days) // 2]
    out = dict(n_events=int(len(e)), first=str(pd.Timestamp(days[0]).date()), last=str(pd.Timestamp(days[-1]).date()))
    for name, part in (("design", e[e.d0 < mid]), ("validation", e[e.d0 >= mid])):
        daily = part.groupby("d0").car.mean()
        t = nw_mean(daily.to_numpy(), lag=max(1, h // 5))
        ci = bootstrap_ci(daily.to_numpy(), block=2)
        out[name] = dict(n_obs=int(len(part)), n_days=int(len(daily)), eff_n=int(len(daily)) if len(part) >= MIN_EVENTS else 0,
                         effect=t["mean"], ci_lo=ci[0], ci_hi=ci[1], t=t["t"], p=t["p"],
                         hit_signal=float((part.car > 0).mean()) if len(part) else np.nan, mean_ar0=float(part.ar0.mean()))
        if len(part) < MIN_EVENTS or len(daily) < MIN_EVENT_DAYS:
            out[name]["eff_n"] = 0
    out["method"] = (f"Event study: CAR over sessions +1..+{h} after the event day, market-adjusted; averaged per event day; "
                     "Newey-West t; bootstrap 95% interval; first half vs second half of the event dates")
    return out


# ---------------- macro association ----------------
def macro_test(row, side):
    """row: a sensitivity row (per_1sd_pct, t, weeks). Association only."""
    t = row.get("t", np.nan)
    eff = row.get("per_1sd_pct", np.nan)
    base = dict(effect=eff, t=t, n_obs=row.get("weeks"), method="Weekly OLS of returns on the factor's change with the Nifty as control "
                "(research/macro_impact.py, last 3 years). Association, in-sample only.")
    if not np.isfinite(t):
        return dict(base, status=INSUFF, why="sensitivity not estimated")
    if abs(t) >= 2:
        return dict(base, status=PARTIAL, why="clear historical association (|t| >= 2) but not tested out of sample; direction depends on the factor's move")
    return dict(base, status=NOT_SUPPORTED, why=f"no clear association (t = {t:.1f})")

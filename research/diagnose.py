"""Framework diagnostic: where (if anywhere) is there a directional edge? Three tests on the Market Lab panel (2015-2026).

All targets are next-session returns RELATIVE to that day's average stock (the market move removed), so a signal has to
pick winners vs losers, not just follow Nifty.  cc = close to next close (delivery, ~0.30% round-trip cost),
oc = next open to next close (intraday, ~0.06%).

1 Feature groups  daily rank correlation (IC) of each feature with the relative return, its t-stat, stability by period,
                  and the top-fifth minus bottom-fifth spread in basis points.
2 Events          announcement categories and results surprise: relative return over the next 1 and 5 sessions after
                  a tradeable entry (the close of the first session that closes after the filing), plus drift vs reversal
                  given the first reaction.
3 Methods         walk-forward by year (train on all earlier years, test on the year): equal-weight nearest-neighbour
                  matching (how Market Lab works) vs ridge (learned linear weights) vs gradient boosting. Daily IC and
                  the top-10 long book's return after costs vs the average stock and vs Nifty.

Writes results/diagnose/report.json and prints a summary. Run: python -m research.diagnose  [quick=1]"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "diagnose"
COST = {"cc": 0.0030, "oc": 0.0006}
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def load_panel():
    sys.path.insert(0, str(BASE))
    from marketlab.features import BASE_GROUPS, CANDIDATES
    groups = dict(BASE_GROUPS, **CANDIDATES)
    feats = [f for g in groups.values() for f in g]
    p = pd.read_parquet(BASE / "data" / "marketlab" / "panel.parquet", columns=["symbol", "date", "sector", "cc", "oc"] + feats)
    for c in feats + ["cc", "oc"]:
        p[c] = p[c].astype("float32")
    p = p.dropna(subset=["cc"])
    p = p[p.cc.abs() < 0.35]                                     # drop split/bad-print outliers
    for t in ("cc", "oc"):
        p[f"{t}_x"] = p[t] - p.groupby("date")[t].transform("mean")
    return p, groups, feats


def daily_ic(p, cols, target):
    """Mean daily Spearman IC per column (ranks within each day)."""
    r = p[["date", target] + cols].copy()
    g = r.groupby("date")
    rk = g[[target] + cols].rank(pct=True)
    rk["date"] = r.date.values
    rk = rk[rk.groupby("date")[target].transform("size") >= 50]
    m = rk.groupby("date").transform("mean")
    d = rk[[target] + cols] - m[[target] + cols]
    out = {}
    for c in cols:
        ok = d[c].notna() & d[target].notna()
        x = (d.loc[ok, c] * d.loc[ok, target]).groupby(rk.loc[ok, "date"]).sum()
        v = np.sqrt((d.loc[ok, c] ** 2).groupby(rk.loc[ok, "date"]).sum() * (d.loc[ok, target] ** 2).groupby(rk.loc[ok, "date"]).sum())
        out[c] = (x / v).replace([np.inf, -np.inf], np.nan).dropna()
    return out


def spread_bps(p, col, target):
    q = p.groupby("date")[col].rank(pct=True)
    top, bot = p.loc[q > 0.8, [target]].assign(d=p.date), p.loc[q <= 0.2, [target]].assign(d=p.date)
    s = top.groupby("d")[target].mean() - bot.groupby("d")[target].mean()
    return float(1e4 * s.mean()) if len(s) else None


def test1(p, groups, targets=("cc_x", "oc_x")):
    log("Test 1: feature groups vs next-session relative return")
    feats = [f for g in groups.values() for f in g]
    res = {}
    for t in targets:
        ics = daily_ic(p, feats, t)
        rows = []
        for g, fs in groups.items():
            for f in fs:
                s = ics[f]
                if len(s) < 100:
                    rows.append(dict(group=g, feature=f, days=int(len(s))))
                    continue
                yr = s.groupby(s.index.year).mean()
                per = {k: round(float(s[(s.index.year >= a) & (s.index.year <= b)].mean()), 4) for k, (a, b) in
                       {"2015-18": (2015, 2018), "2019-22": (2019, 2022), "2023-26": (2023, 2026)}.items()}
                rows.append(dict(group=g, feature=f, days=int(len(s)), ic=round(float(s.mean()), 4),
                                 t=round(float(s.mean() / (s.std() / np.sqrt(len(s)))), 1),
                                 same_sign_years=f"{int((np.sign(yr) == np.sign(s.mean())).sum())}/{len(yr)}", **per,
                                 spread_bps=round(spread_bps(p, f, t[:2]), 1)))
        res[t] = rows
    return res


def load_events(p):
    a = pd.read_parquet(BASE / "data" / "announcements.parquet", columns=["published", "symbol", "category"])
    a = a[a.symbol.astype(bool) & a.category.notna()]
    a["published"] = pd.to_datetime(a.published)
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "close"])
    px["date"] = pd.to_datetime(px.date)
    px = px.sort_values(["symbol", "date"])
    nf = pd.read_parquet(BASE / "data" / "index.parquet", columns=["date", "nifty"]).dropna()
    nf["date"] = pd.to_datetime(nf.date)
    nf = nf.set_index("date").nifty.sort_index()
    return a, px, nf


def test2(p):
    log("Test 2: events (announcements, results surprise)")
    a, px, nf = load_events(p)
    days = pd.DatetimeIndex(sorted(px.date.unique()))
    # entry session: the first session whose 15:30 close is after the filing
    close_t = days + pd.Timedelta(hours=15, minutes=30)
    a = a[(a.published >= days[0]) & (a.published < close_t[-1])]
    idx = np.searchsorted(close_t.values, a.published.values, side="left")
    a = a.assign(entry=days[idx]).drop_duplicates(["symbol", "category", "entry"])
    c = px.pivot_table(index="date", columns="symbol", values="close").reindex(days)
    n = nf.reindex(days).ffill()
    out = []
    for k in (1, 5):
        fwd = c.shift(-k) / c - 1
        mk = fwd.mean(axis=1)                                      # average stock (equal weight), same as the panel
        fn = n.shift(-k) / n - 1
        pos = pd.Series(np.arange(len(days)), index=days)
        ii, jj = pos.reindex(a.entry).values, c.columns.get_indexer(a.symbol)
        ok = (jj >= 0) & ~np.isnan(ii)
        r = np.full(len(a), np.nan)
        r[ok] = fwd.values[ii[ok].astype(int), jj[ok]]
        a[f"r{k}"] = r
        a[f"x{k}"] = r - mk.reindex(a.entry).values
        a[f"n{k}"] = r - fn.reindex(a.entry).values
    # first reaction: entry-day return (previous close -> entry close), relative to the average stock
    d0 = c / c.shift(1) - 1
    ii, jj = pd.Series(np.arange(len(days)), index=days).reindex(a.entry).values, c.columns.get_indexer(a.symbol)
    ok = (jj >= 0) & ~np.isnan(ii)
    r0 = np.full(len(a), np.nan)
    r0[ok] = d0.values[ii[ok].astype(int), jj[ok]]
    a["x0"] = r0 - d0.mean(axis=1).reindex(a.entry).values
    a = a.dropna(subset=["x1"])
    a = a[a.x1.abs() < 0.35]
    for cat, g in a.groupby("category"):
        if len(g) < 200:
            continue
        row = dict(event=cat, n=int(len(g)))
        for k in (1, 5):
            x = g[f"x{k}"].dropna()
            row[f"rel_{k}d_bps"] = round(1e4 * float(x.mean()), 1)
            row[f"t_{k}d"] = round(float(x.mean() / (x.std() / np.sqrt(len(x)))), 1)
            row[f"vs_nifty_{k}d_bps"] = round(1e4 * float(g[f"n{k}"].mean()), 1)
        up, dn = g[g.x0 > 0.02], g[g.x0 < -0.02]                    # strong first reaction: drift or reversal next 5 days?
        row["after_up_reaction_5d_bps"] = round(1e4 * float(up.x5.mean()), 1) if len(up) >= 50 else None
        row["after_down_reaction_5d_bps"] = round(1e4 * float(dn.x5.mean()), 1) if len(dn) >= 50 else None
        row["n_up"], row["n_down"] = int(len(up)), int(len(dn))
        out.append(row)
    # results surprise from the panel (results filed today; next-session relative return)
    r = p[(p.e_res_s > 0) | (p.e_res_e > 0)].dropna(subset=["e_sue"])
    sue = []
    for lab, m in (("SUE > +1", r.e_sue > 1), ("SUE 0..+1", (r.e_sue > 0) & (r.e_sue <= 1)), ("SUE -1..0", (r.e_sue <= 0) & (r.e_sue >= -1)), ("SUE < -1", r.e_sue < -1)):
        g = r[m]
        if len(g) >= 50:
            sue.append(dict(bucket=lab, n=int(len(g)), cc_rel_bps=round(1e4 * float(g.cc_x.mean()), 1),
                            t=round(float(g.cc_x.mean() / (g.cc_x.std() / np.sqrt(len(g)))), 1), oc_rel_bps=round(1e4 * float(g.oc_x.mean()), 1)))
    return dict(events=sorted(out, key=lambda x: -abs(x["t_5d"])), results_surprise=sue)


def book(day_df, score, target, k=10):
    """Top-k long book on each day: mean target (relative), and the raw next-session return."""
    d = day_df.assign(s=score)
    top = d[d.s.rank(ascending=False, method="first") <= k]
    return float(top[f"{target}_x"].mean()), float(top[target].mean())


def test3(p, feats, quick=False, targets=("cc", "oc"), years=range(2019, 2027)):
    log("Test 3: methods, walk-forward by year")
    import lightgbm as lgb
    X = p[feats].astype("float32")
    years = list(years)
    rng = np.random.default_rng(0)
    res = {}
    for t in targets:
        y = p[f"{t}_x"].values
        rows = []
        for yr in years:
            tr = (p.date.dt.year < yr).values & ~np.isnan(y)
            te = (p.date.dt.year == yr).values & ~np.isnan(y)
            if tr.sum() < 50000 or te.sum() < 5000:
                continue
            mu, sd = np.nanmean(X.values[tr], 0), np.nanstd(X.values[tr], 0) + 1e-6
            Z = np.nan_to_num((X.values - mu) / sd).clip(-5, 5).astype("float32")
            # ridge: learned linear weights
            Zt, yt = Z[tr], y[tr]
            w = np.linalg.solve(Zt.T @ Zt + 1e3 * np.eye(Z.shape[1]), Zt.T @ yt)
            # gradient boosting (sampled for speed)
            si = rng.choice(np.where(tr)[0], size=min(int(tr.sum()), 400000 if not quick else 120000), replace=False)
            gb = lgb.LGBMRegressor(n_estimators=250 if not quick else 80, learning_rate=0.05, num_leaves=31, min_child_samples=500,
                                   subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1)
            gb.fit(Z[si], y[si])
            te_days = sorted(p.date[te].unique())
            ev_days = te_days[:: max(1, len(te_days) // (25 if not quick else 8))]
            lib = rng.choice(np.where(tr)[0], size=min(int(tr.sum()), 150000), replace=False)
            L, Ly = Z[lib], y[lib]
            Ln = (L ** 2).sum(1)
            stats = {m: dict(ic=[], book_x=[], book_raw=[]) for m in ("knn_equal", "ridge", "boosting", "momentum_1d", "random")}
            for dday in ev_days:
                m = (p.date == dday).values & te
                if m.sum() < 100:
                    continue
                Q, dd = Z[m], p.loc[m, ["cc", "oc", "cc_x", "oc_x"]]
                dist = (Q ** 2).sum(1)[:, None] + Ln[None, :] - 2 * Q @ L.T
                nn = np.argpartition(dist, 100, axis=1)[:, :100]
                sc = {"knn_equal": Ly[nn].mean(1), "ridge": Q @ w, "boosting": gb.predict(Q),
                      "momentum_1d": p.loc[m, "f_ret1"].fillna(0).values, "random": rng.random(m.sum())}
                for k2, s in sc.items():
                    ic = pd.Series(s).rank().corr(pd.Series(dd[f"{t}_x"].values).rank())
                    bx, br = book(dd.reset_index(drop=True), s, t)
                    stats[k2]["ic"].append(ic); stats[k2]["book_x"].append(bx); stats[k2]["book_raw"].append(br)
            for k2, s in stats.items():
                rows.append(dict(year=yr, method=k2, days=len(s["ic"]), ic=round(float(np.nanmean(s["ic"])), 4),
                                 top10_rel_bps=round(1e4 * float(np.nanmean(s["book_x"])), 1),
                                 top10_after_cost_bps=round(1e4 * (float(np.nanmean(s["book_raw"])) - COST[t]), 1)))
            log(f"  {t} {yr}: done")
        res[t] = dict(by_year=rows)
    imp = sorted(zip(feats, gb.feature_importances_.tolist()), key=lambda x: -x[1])[:12]
    res["boosting_top_features"] = imp
    return res


def summarize3(rows):
        df = pd.DataFrame(rows)
        summ = df.groupby("method").agg(ic=("ic", "mean"), ic_pos_years=("ic", lambda v: f"{int((v > 0).sum())}/{len(v)}"),
                                        top10_rel_bps=("top10_rel_bps", "mean"), top10_after_cost_bps=("top10_after_cost_bps", "mean")).round(3)
        return summ.reset_index().to_dict("records")


def main(argv):
    """Runs in parts so each step finishes quickly:  part=t1cc | t1oc | t2 | t3:cc:2019 ... | summary  (no part = all)."""
    kw = dict(a.split("=", 1) for a in argv if "=" in a)
    part, quick = kw.get("part", "all"), kw.get("quick") == "1"
    PARTS = OUT / "parts"
    PARTS.mkdir(parents=True, exist_ok=True)
    if part == "summary":
        return summary(PARTS)
    p, groups, feats = load_panel()
    log(f"panel: {len(p):,} stock-days, {p.date.nunique()} days, {p.date.min():%Y-%m-%d} .. {p.date.max():%Y-%m-%d}")
    if quick:
        p = p[p.date >= "2021-01-01"]
    base_feats = [f for g, fs in groups.items() if g not in ("Delivery", "Valuation") for f in fs]
    todo = [part] if part != "all" else ["t1cc", "t1oc", "t2"] + [f"t3:{t}:{y}" for t in ("cc", "oc") for y in range(2019, 2027)]
    for x in todo:
        if x.startswith("t1"):
            r = test1(p, groups, (f"{x[2:]}_x",))
        elif x == "t2":
            r = test2(p)
        else:
            _, t, y = x.split(":")
            r = test3(p, base_feats, quick, (t,), (int(y),))
        (PARTS / f"{x.replace(':', '_')}.json").write_text(json.dumps(r, default=str))
        log(f"part {x} saved")
    if part == "all":
        summary(PARTS)


def summary(PARTS):
    rd = lambda n: json.loads((PARTS / f"{n}.json").read_text()) if (PARTS / f"{n}.json").exists() else None
    rep = dict(made=str(pd.Timestamp.now().floor("s")), test1={}, test3={})
    for t in ("cc", "oc"):
        r = rd(f"t1{t}")
        if r:
            rep["test1"][f"{t}_x"] = r[f"{t}_x"]
        rows, imp = [], None
        for y in range(2019, 2027):
            r = rd(f"t3_{t}_{y}")
            if r:
                rows += r[t]["by_year"]
                imp = r["boosting_top_features"]
        if rows:
            rep["test3"][t] = dict(by_year=rows, summary=summarize3(rows), boosting_top_features=imp)
    rep["test2"] = rd("t2")
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    for t in ("cc_x", "oc_x"):
        print(f"\n=== TEST 1 {t}: features with |t| >= 3 ===")
        for r in sorted([r for r in rep["test1"].get(t, []) if r.get("t") is not None and abs(r["t"]) >= 3], key=lambda r: -abs(r["t"])):
            print({k: r[k] for k in ("group", "feature", "ic", "t", "same_sign_years", "2015-18", "2019-22", "2023-26", "spread_bps") if k in r})
    if rep["test2"]:
        print("\n=== TEST 2: events (|t_5d| >= 2 or strong reaction split) ===")
        for r in rep["test2"]["events"]:
            print(r)
        for r in rep["test2"]["results_surprise"]:
            print(r)
    for t, v in rep["test3"].items():
        print(f"\n=== TEST 3 {t}: methods, mean of yearly folds ===")
        for r in v["summary"]:
            print(" ", r)
        print("  by year (ic):", {m: [r["ic"] for r in v["by_year"] if r["method"] == m] for m in ("knn_equal", "ridge", "boosting")})
        print("  boosting top features:", v["boosting_top_features"][:8])


if __name__ == "__main__":
    main(sys.argv[1:])

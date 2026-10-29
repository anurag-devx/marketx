"""Model v2 research backtest: does a better scoring framework make money after costs?

Every trading day 2019-2026, each stock gets a score from one of three scorers, trained each year only on earlier years:
  rules     fixed-direction rank composite: the sign of each feature's past relation to next-day relative return is
            learned from the training years, weights are equal (transparent: 'fade big moves, follow the sector ...')
  ridge     learned linear weights
  boosting  gradient-boosted trees (lightgbm)
plus baselines: random, momentum (yesterday's gainers), calm (least volatile), and Nifty buy-and-hold.

Targets: cc_x / oc_x = next-session return relative to that day's average stock.
Books (equal money, rebuilt daily):
  cc_long  top N, buy at today's close, sell at next close; cost 0.30% + 0.10% slippage round trip (delivery)
  oc_long  top N, buy next open, sell next close; cost 0.06% + 0.05% (intraday)
  oc_ls    top N long and bottom N short intraday (cash-market intraday shorts), same costs on both legs
New features (point-in-time, from data/announcements.parquet):
  ev_react  first-reaction relative return after a results / board / order / management / press / dividend filing,
            carried for 5 sessions;  ev_age  sessions since that filing (0-4);  res_react  same, results only
  ins_5d    insider / shareholding filings in the last 5 sessions
Caveat: the stock list is today's Nifty 500 (survivorship), which flatters every absolute return, baselines included.

Run in parts (each well under 3 minutes):  python -m research.model2 part=prep | part=fold:2019 ... fold:2026 | part=report"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "model2"
COST = {"cc": 0.0030 + 0.0010, "oc": 0.0006 + 0.0005}
EVENTS = {"Results", "Board meeting", "Order / contract", "Management change", "Press release", "Dividend"}
TOPN = (10, 20)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def prep():
    """Panel + event features -> results/model2/data.parquet (float32)."""
    sys.path.insert(0, str(BASE))
    from marketlab.features import BASE_GROUPS, CANDIDATES
    feats = [f for g in BASE_GROUPS.values() for f in g] + CANDIDATES["Delivery"]
    p = pd.read_parquet(BASE / "data" / "marketlab" / "panel.parquet", columns=["symbol", "date", "cc", "oc"] + feats)
    p = p.dropna(subset=["cc"])
    p = p[p.cc.abs() < 0.35]
    days = pd.DatetimeIndex(sorted(p.date.unique()))
    pos = pd.Series(np.arange(len(days)), index=days)
    # first reaction: relative close-to-close return on the entry session (the first close after the filing)
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "close"])
    px["date"] = pd.to_datetime(px.date)
    c = px.pivot_table(index="date", columns="symbol", values="close").reindex(days)
    r0 = c / c.shift(1) - 1
    x0 = r0.sub(r0.mean(axis=1), axis=0)
    a = pd.read_parquet(BASE / "data" / "announcements.parquet", columns=["published", "symbol", "category"])
    a = a[a.symbol.astype(bool) & a.category.notna()]
    a["published"] = pd.to_datetime(a.published)
    close_t = days + pd.Timedelta(hours=15, minutes=30)
    a = a[(a.published >= days[0]) & (a.published < close_t[-1])]
    a["entry"] = days[np.searchsorted(close_t.values, a.published.values, side="left")]
    a = a.drop_duplicates(["symbol", "category", "entry"])
    jj = c.columns.get_indexer(a.symbol)
    ii = pos.reindex(a.entry).values
    ok = jj >= 0
    a = a[ok]
    a["react"] = x0.values[ii[ok], jj[ok]]
    rows = []
    for name, cats in (("ev", EVENTS), ("res", {"Results"})):
        e = a[a.category.isin(cats)].sort_values("react", key=abs).drop_duplicates(["symbol", "entry"], keep="last")
        for k in range(5):                                   # carry for 5 sessions (entry day = age 0)
            i2 = pos.reindex(e.entry).values + k
            m = i2 < len(days)
            rows.append(pd.DataFrame({"symbol": e.symbol.values[m], "date": days[i2[m].astype(int)], f"{name}_react": e.react.values[m], f"{name}_age": k}))
    ev = pd.concat([r for r in rows if "ev_react" in r]).sort_values("ev_age").drop_duplicates(["symbol", "date"])
    rs = pd.concat([r for r in rows if "res_react" in r]).sort_values("res_age").drop_duplicates(["symbol", "date"])
    ins = a[a.category == "Shareholding / insider"].groupby(["symbol", "entry"]).size().rename("n").reset_index()
    ins_rows = []
    for k in range(5):
        i2 = pos.reindex(ins.entry).values + k
        m = i2 < len(days)
        ins_rows.append(pd.DataFrame({"symbol": ins.symbol.values[m], "date": days[i2[m].astype(int)], "n": ins.n.values[m]}))
    ins5 = pd.concat(ins_rows).groupby(["symbol", "date"]).n.sum().rename("ins_5d").reset_index()
    p = p.merge(ev, on=["symbol", "date"], how="left").merge(rs[["symbol", "date", "res_react"]], on=["symbol", "date"], how="left") \
         .merge(ins5, on=["symbol", "date"], how="left")
    p["ins_5d"] = p.ins_5d.fillna(0)
    for t in ("cc", "oc"):
        p[f"{t}_x"] = p[t] - p.groupby("date")[t].transform("mean")
    num = [x for x in p.columns if x not in ("symbol", "date")]
    p[num] = p[num].astype("float32")
    OUT.mkdir(parents=True, exist_ok=True)
    p.to_parquet(OUT / "data.parquet", index=False)
    log(f"prep: {len(p):,} rows, features {len(num) - 4}; events {p.ev_react.notna().sum():,}, results {p.res_react.notna().sum():,}, insider {int((p.ins_5d > 0).sum()):,}")


def features(p):
    return [x for x in p.columns if x not in ("symbol", "date", "cc", "oc", "cc_x", "oc_x")]


def rules_fit(p, feats, target):
    """Sign of each feature's mean daily rank correlation with the target over the training rows."""
    d = p[["date", target] + feats]
    rk = d.groupby("date")[[target] + feats].rank(pct=True)
    rk = rk - rk.groupby(d.date).transform("mean")
    sign = {}
    for f in feats:
        ok = rk[f].notna()
        sign[f] = float(np.sign((rk.loc[ok, f] * rk.loc[ok, target]).mean())) if ok.sum() > 10000 else 0.0
    return sign


def fold(year):
    import lightgbm as lgb
    p = pd.read_parquet(OUT / "data.parquet")
    feats = features(p)
    tr, te = p[p.date.dt.year < year], p[p.date.dt.year == year]
    if len(te) == 0:
        return
    rng = np.random.default_rng(year)
    out = {}
    for t in ("cc", "oc"):
        y = tr[f"{t}_x"].values
        ok = ~np.isnan(y)
        X = tr[feats].values
        mu, sd = np.nanmean(X, 0), np.nanstd(X, 0) + 1e-6
        Z = np.nan_to_num((X - mu) / sd).clip(-5, 5)
        w = np.linalg.solve(Z[ok].T @ Z[ok] + 1e3 * np.eye(len(feats)), Z[ok].T @ y[ok])
        si = rng.choice(np.where(ok)[0], size=min(int(ok.sum()), 400000), replace=False)
        gb = lgb.LGBMRegressor(n_estimators=250, learning_rate=0.05, num_leaves=31, min_child_samples=500, subsample=0.8,
                               subsample_freq=1, colsample_bytree=0.8, verbose=-1).fit(tr[feats].values[si], y[si])
        sign = rules_fit(tr.iloc[si[:200000]], feats, f"{t}_x")
        Zte = np.nan_to_num((te[feats].values - mu) / sd).clip(-5, 5)
        s = pd.DataFrame({"date": te.date.values, "symbol": te.symbol.values, "cc": te.cc.values, "oc": te.oc.values,
                          "ridge": Zte @ w, "boosting": gb.predict(te[feats].values), "random": rng.random(len(te)),
                          "momentum": te.f_ret1.fillna(0).values, "calm": -te.f_rv.fillna(0).values})
        rk = te.groupby("date")[feats].rank(pct=True) - 0.5
        s["rules"] = sum(sign[f] * rk[f].fillna(0).values for f in feats if sign[f])
        s[["date", "symbol", "cc", "oc", "rules", "ridge", "boosting", "random"]].astype({m: "float32" for m in ("cc", "oc", "rules", "ridge", "boosting", "random")}) \
            .to_parquet(OUT / f"scores_{t}_{year}.parquet", index=False)
        out[t] = (s, sign, sorted(zip(feats, gb.feature_importances_.tolist()), key=lambda x: -x[1])[:10])
    # daily books
    books = []
    for t in ("cc", "oc"):
        s = out[t][0]
        for m in ("rules", "ridge", "boosting", "random", "momentum", "calm"):
            g = s.groupby("date")
            for n in TOPN:
                top = s[g[m].rank(ascending=False, method="first") <= n]
                lr = top.groupby("date")[t].mean()
                if t == "cc":
                    books.append(pd.DataFrame({"date": lr.index, "book": f"cc_long_top{n}", "method": m, "gross": lr.values, "net": lr.values - COST["cc"]}))
                else:
                    bot = s[g[m].rank(ascending=True, method="first") <= n].groupby("date")["oc"].mean()
                    books.append(pd.DataFrame({"date": lr.index, "book": f"oc_long_top{n}", "method": m, "gross": lr.values, "net": lr.values - COST["oc"]}))
                    ls = (lr - bot.reindex(lr.index)) / 2                       # half the money long, half short
                    books.append(pd.DataFrame({"date": lr.index, "book": f"oc_ls_top{n}", "method": m, "gross": ls.values, "net": ls.values - COST["oc"]}))
        ic = s.groupby("date").apply(lambda d: pd.Series({m: d[m].rank().corr(d[f"{t}"].rank()) for m in ("rules", "ridge", "boosting", "momentum", "calm")}))
        ic.to_parquet(OUT / f"ic_{t}_{year}.parquet")
    pd.concat(books).to_parquet(OUT / f"books_{year}.parquet", index=False)
    meta = {t: dict(rules_signs={k: v for k, v in out[t][1].items()}, boosting_top=out[t][2]) for t in out}
    (OUT / f"meta_{year}.json").write_text(json.dumps(meta, default=float))
    log(f"fold {year}: {te.date.nunique()} days")


def stats(r):
    r = r.dropna()
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    yrs = len(r) / 248
    return dict(days=len(r), avg_day_bps=round(1e4 * r.mean(), 1), win_days=round(100 * (r > 0).mean(), 1),
                cagr=round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 1) if yrs > 0 and eq.iloc[-1] > 0 else None,
                sharpe=round(float(r.mean() / r.std() * np.sqrt(248)), 2) if r.std() > 0 else None,
                max_dd=round(100 * dd, 1), total=round(100 * (eq.iloc[-1] - 1), 1))


def report():
    b = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("books_*.parquet"))])
    nf = pd.read_parquet(BASE / "data" / "index.parquet", columns=["date", "nifty"]).dropna()
    nf["date"] = pd.to_datetime(nf.date)
    nf = nf.set_index("date").nifty.sort_index()
    nret = (nf.shift(-1) / nf - 1)                                   # Nifty next session, same timing as the cc book
    days = sorted(b.date.unique())
    rep = dict(made=str(pd.Timestamp.now().floor("s")), period=f"{pd.Timestamp(days[0]):%Y-%m-%d} .. {pd.Timestamp(days[-1]):%Y-%m-%d}",
               costs=COST, nifty=stats(nret.reindex(days)), books=[], by_year=[])
    for (bk, m), g in b.groupby(["book", "method"]):
        r = g.set_index("date").net.sort_index()
        rep["books"].append(dict(book=bk, method=m, **stats(r), gross_avg_day_bps=round(1e4 * g.gross.mean(), 1)))
        for y, gy in r.groupby(r.index.year):
            rep["by_year"].append(dict(book=bk, method=m, year=int(y), avg_day_bps=round(1e4 * gy.mean(), 1), total=round(100 * ((1 + gy).prod() - 1), 1)))
    ics = {}
    for t in ("cc", "oc"):
        fs = sorted(OUT.glob(f"ic_{t}_*.parquet"))
        if fs:
            ic = pd.concat([pd.read_parquet(f) for f in fs])
            ics[t] = {m: dict(ic=round(float(ic[m].mean()), 4), pos_years=f"{int((ic[m].groupby(ic.index.year).mean() > 0).sum())}/{ic.index.year.nunique()}")
                      for m in ic.columns}
    rep["ic"] = ics
    metas = {int(f.stem.split("_")[1]): json.loads(f.read_text()) for f in OUT.glob("meta_*.json")}
    last = metas[max(metas)] if metas else {}
    rep["latest_rules_signs"] = {t: {k: v for k, v in last[t]["rules_signs"].items() if v} for t in last}
    rep["latest_boosting_top"] = {t: last[t]["boosting_top"] for t in last}
    (OUT / "report.json").write_text(json.dumps(rep, indent=1, default=str))
    print("period", rep["period"], "costs", COST)
    print("Nifty buy-and-hold:", rep["nifty"])
    print("IC:", json.dumps(ics))
    for bk in sorted({x["book"] for x in rep["books"]}):
        print(f"\n== {bk} (after costs) ==")
        for x in sorted([x for x in rep["books"] if x["book"] == bk], key=lambda x: -(x["sharpe"] or -9)):
            print({k: x[k] for k in ("method", "avg_day_bps", "gross_avg_day_bps", "win_days", "cagr", "sharpe", "max_dd")})
    print("\n== by year, avg bps/day after costs (rules / boosting / random) ==")
    by = pd.DataFrame(rep["by_year"])
    for bk in ("cc_long_top10", "oc_long_top10", "oc_ls_top10"):
        t = by[(by.book == bk) & by.method.isin(["rules", "ridge", "boosting", "random", "calm"])].pivot(index="year", columns="method", values="avg_day_bps")
        print(bk); print(t.to_string())
    print("\nrules directions (latest):", rep["latest_rules_signs"])
    print("boosting top (latest):", rep["latest_boosting_top"])


def p920():
    """From the 5-minute bars: each stock's price at 09:20 and 09:30 (close of the 09:15 and 09:25 bars) -> p920.parquet."""
    d5 = BASE / "data" / "intraday" / "5min"
    out = []
    for f in sorted(d5.glob("*.parquet")):
        b = pd.read_parquet(f, columns=["ts", "close"])
        b["ts"] = pd.to_datetime(b.ts)
        hm = b.ts.dt.hour.values * 100 + b.ts.dt.minute.values
        for lab, t in (("p920", 915), ("p930", 925)):
            x = b[hm == t]
            out.append(pd.DataFrame({"symbol": f.stem, "date": x.ts.dt.normalize().values, "kind": lab, "px": x.close.values.astype("float32")}))
    d = pd.concat(out).pivot_table(index=["symbol", "date"], columns="kind", values="px").reset_index()
    d.to_parquet(OUT / "p920.parquet", index=False)
    log(f"p920: {len(d):,} stock-days from {d.symbol.nunique()} stocks, {d.date.min():%Y-%m-%d} .. {d.date.max():%Y-%m-%d}")


def stress():
    """Intraday books under harsher assumptions: liquid stocks only, later entry, higher slippage; by year."""
    s = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("scores_oc_*.parquet"))])
    days = pd.DatetimeIndex(sorted(s.date.unique()))
    nxt = pd.Series(days[1:].append(pd.DatetimeIndex([days[-1] + pd.Timedelta(days=1)])), index=days)
    s["trade_day"] = s.date.map(nxt)
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "close", "volume"])
    px["date"] = pd.to_datetime(px.date)
    px = px.sort_values(["symbol", "date"])
    px["tv"] = (px.close * px.volume).groupby(px.symbol).transform(lambda v: v.rolling(20, min_periods=10).mean())
    px["liq_rank"] = px.groupby("date").tv.rank(ascending=False)
    s = s.merge(px[["symbol", "date", "liq_rank"]], on=["symbol", "date"], how="left")
    nc = px[["symbol", "date", "close"]].rename(columns={"date": "trade_day", "close": "close_next"})
    p9 = pd.read_parquet(OUT / "p920.parquet").rename(columns={"date": "trade_day"})
    s = s.merge(nc, on=["symbol", "trade_day"], how="left").merge(p9, on=["symbol", "trade_day"], how="left")
    s["r_open"] = s.oc
    for k in ("p920", "p930"):
        s[f"r_{k}"] = (s.close_next / s[k] - 1).where(lambda r: r.abs() < 0.2)
    have5 = s.groupby("date").p920.transform(lambda v: v.notna().mean()) > 0.8
    rows = []
    SLIP = {"0.05%": 0.0005, "0.10%": 0.0010, "0.20%": 0.0020}           # per side, on top of 0.06% charges round trip
    for uni, um in (("all 500", np.ones(len(s), bool)), ("liquid 200", (s.liq_rank <= 200).values)):
        for entry in ("r_open", "r_p920", "r_p930"):
            m = um & s[entry].notna().values & (have5.values if entry != "r_open" else True)
            x = s[m]
            g = x.groupby("date")
            for meth in ("boosting", "rules", "random"):
                for n in (10, 20):
                    top = x[g[meth].rank(ascending=False, method="first") <= n].groupby("date")[entry].mean()
                    bot = x[g[meth].rank(ascending=True, method="first") <= n].groupby("date")[entry].mean()
                    ls = ((top - bot.reindex(top.index)) / 2).dropna()
                    for sl, v in SLIP.items():
                        net = ls - (0.0006 + 2 * v)
                        st = stats(net)
                        yr = net.groupby(net.index.year).mean()
                        rows.append(dict(universe=uni, entry=entry[2:], method=meth, n=n, slip=sl, **st,
                                         y2019_21=round(1e4 * net[net.index.year <= 2021].mean(), 1),
                                         y2022_24=round(1e4 * net[(net.index.year >= 2022) & (net.index.year <= 2024)].mean(), 1),
                                         y2025_26=round(1e4 * net[net.index.year >= 2025].mean(), 1),
                                         neg_years=int((yr < 0).sum()), years=len(yr)))
        log(f"stress: {uni} done")
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "stress.csv", index=False)
    pd.set_option("display.width", 250)
    cols = ["universe", "entry", "method", "n", "slip", "days", "avg_day_bps", "sharpe", "max_dd", "cagr", "y2019_21", "y2022_24", "y2025_26", "neg_years"]
    print(r[(r.method == "boosting") & (r.n == 10)][cols].to_string(index=False))
    print(r[(r.method != "boosting") & (r.n == 10) & (r.slip == "0.10%")][cols].to_string(index=False))
    print(r[(r.method == "boosting") & (r.n == 20) & (r.slip == "0.10%")][cols].to_string(index=False))


if __name__ == "__main__":
    kw = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    part = kw.get("part", "report")
    if part == "prep":
        prep()
    elif part == "p920":
        p920()
    elif part == "stress":
        stress()
    elif part.startswith("fold:"):
        fold(int(part.split(":")[1]))
    else:
        report()

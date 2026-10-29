"""Swing research backtest: buy-only, hold 5 / 10 / 20 sessions, liquid stocks, realistic entry and costs.

Each rebalance day d (every H sessions), stocks in the liquid 200 (20-day traded value rank at d) are scored; the top N
are bought at the 09:20 price of d+1 and sold at the 09:20 price H sessions later (5-minute bars, split-adjusted with the
daily adjustment factor). Costs: 0.25% per side on the money that changes hands (0.30% delivery charges round trip +
0.10% slippage each way), so names kept from one rebalance to the next pay nothing.
Scorers, trained each year only on earlier years (labels that end inside the test year are dropped):
  boosting  gradient-boosted trees on all features
  rules     each feature's direction learned from the training years, equal weights on ranks
  momentum  12-month return skipping the last month (the classic rule), baseline
  random    baseline;  Nifty buy-and-hold over the same days
Features at the close of d (nothing later):
  momentum r21 r63 r126 r252_21, distance from 52-week high, 63-day volatility, traded value (log) and its trend,
  sector 63-day strength vs market, results surprise in the last 60 sessions (SUE, days since), insider/shareholding filings
  in the last 20 sessions, delivery % and delivered shares vs normal (20-day mean, 2024+ only), plus today's panel features
  (f_ret1, f_ret5, f_rng, f_rv, f_vol, s_r5, m_r5, m_vix).
Target: forward H-session return relative to the average liquid stock.
Caveat: the stock list is today's Nifty 500 (survivorship), which flatters absolute returns; compare against the baselines.
Run in parts:  python -m research.swing part=prep | part=fold:2019 ... | part=report"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "results" / "swing"
SIDE = 0.0025
HS = (5, 10, 20)
NS = (10, 20)
LIQ = 200
T0 = time.time()
PANEL = ["f_ret1", "f_ret5", "f_rng", "f_rv", "f_vol", "s_r5", "m_r5", "m_vix", "e_sue", "e_res_s", "e_res_e", "d_pct", "d_rel"]


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def prep():
    sys.path.insert(0, str(BASE))
    raw = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "open", "high", "low", "close", "volume"])
    raw["date"] = pd.to_datetime(raw.date)
    try:
        from core.quality import apply_corporate_actions
        adj, _ = apply_corporate_actions(raw.copy())
    except Exception:
        adj = raw.copy()
    C = adj.pivot_table(index="date", columns="symbol", values="close").sort_index()
    R = raw.pivot_table(index="date", columns="symbol", values="close").reindex_like(C)
    V = raw.pivot_table(index="date", columns="symbol", values="volume").reindex_like(C)
    factor = (C / R).replace([np.inf, -np.inf], np.nan)
    p9 = pd.read_parquet(BASE / "results" / "model2" / "p920.parquet")
    P9 = p9.pivot_table(index="date", columns="symbol", values="p920").reindex_like(C) * factor   # adjusted 09:20 price
    tv = (R * V).rolling(20, min_periods=10).mean()
    liq = tv.rank(axis=1, ascending=False)
    ret = C.pct_change(fill_method=None)
    F = {"r21": C / C.shift(21) - 1, "r63": C / C.shift(63) - 1, "r126": C / C.shift(126) - 1, "r252_21": C.shift(21) / C.shift(252) - 1,
         "hi52": C / C.rolling(252, min_periods=120).max() - 1, "vol63": ret.rolling(63, min_periods=40).std(),
         "tv_log": np.log(tv), "tv_trend": np.log(tv / (R * V).rolling(120, min_periods=60).mean())}
    # sector strength: sector mean 63-day return minus all stocks
    uni = pd.read_csv(BASE / "data" / "universe.csv")
    scol = next(c for c in uni.columns if c.lower() in ("industry", "sector"))
    sec = uni.set_index(uni.columns[0] if "symbol" not in [c.lower() for c in uni.columns] else next(c for c in uni.columns if c.lower() == "symbol"))[scol]
    r63 = F["r63"]
    smean = r63.T.groupby(sec.reindex(r63.columns).values).mean().T
    F["sec63"] = pd.DataFrame({s: smean[sec.get(s)] if sec.get(s) in smean else np.nan for s in r63.columns}, index=r63.index).sub(r63.mean(axis=1), axis=0)
    long = pd.concat({k: v.stack(dropna=False) for k, v in F.items()}, axis=1)
    long.index.names = ["date", "symbol"]
    long["liq"] = liq.stack(dropna=False)
    # panel features of the day, results surprise carried 60 sessions, delivery 20-day mean
    p = pd.read_parquet(BASE / "data" / "marketlab" / "panel.parquet", columns=["date", "symbol"] + PANEL).set_index(["date", "symbol"])
    long = long.join(p[[c for c in PANEL if not c.startswith(("e_", "d_"))]], how="left")
    sue = p.e_sue.where((p.e_res_s > 0) | (p.e_res_e > 0)).unstack().reindex(C.index)
    long["sue60"] = sue.ffill(limit=60).stack(dropna=False)
    pos = np.repeat(np.arange(len(C), dtype=float)[:, None], sue.shape[1], 1)
    last = pd.DataFrame(np.where(sue.notna().values, pos, np.nan), index=sue.index, columns=sue.columns).ffill(limit=60)
    long["sue_age"] = (pd.DataFrame(pos, index=sue.index, columns=sue.columns) - last).stack(dropna=False)
    for c in ("d_pct", "d_rel"):
        long[c + "20"] = p[c].unstack().reindex(C.index).rolling(20, min_periods=5).mean().stack(dropna=False)
    a = pd.read_parquet(BASE / "data" / "announcements.parquet", columns=["published", "symbol", "category"])
    a = a[a.category == "Shareholding / insider"]
    a["date"] = pd.to_datetime(a.published).dt.normalize()
    ins = a.groupby(["date", "symbol"]).size().unstack().reindex(C.index).fillna(0)
    long["ins20"] = ins.reindex(columns=C.columns).fillna(0).rolling(20, min_periods=1).sum().stack(dropna=False)
    # forward returns, 09:20 to 09:20, entry the session after d
    for H in HS:
        fwd = P9.shift(-(1 + H)) / P9.shift(-1) - 1
        fwd = fwd.where(fwd.abs() < 0.6)
        long[f"y{H}"] = fwd.stack(dropna=False)
    long = long.reset_index()
    long = long[(long.liq <= 300) & long.r63.notna()]
    num = [c for c in long.columns if c not in ("date", "symbol")]
    long[num] = long[num].astype("float32")
    OUT.mkdir(parents=True, exist_ok=True)
    long.to_parquet(OUT / "data.parquet", index=False)
    nf = pd.read_parquet(BASE / "data" / "index.parquet", columns=["date", "nifty"]).dropna()
    nf.to_parquet(OUT / "nifty.parquet", index=False)
    log(f"prep: {len(long):,} rows, {long.date.nunique()} days, features {len([c for c in num if not c.startswith('y') and c != 'liq'])}")


def feats(d):
    return [c for c in d.columns if c not in ("date", "symbol", "liq") and not (c[0] == "y" and c[1:].isdigit())]


def fold(year):
    import lightgbm as lgb
    d = pd.read_parquet(OUT / "data.parquet")
    d = d[d.liq <= LIQ]
    fs = feats(d)
    days = np.array(sorted(d.date.unique()))
    out = []
    for H in HS:
        y = f"y{H}"
        d[f"{y}x"] = d[y] - d.groupby("date")[y].transform("mean")
        test_days = days[(pd.DatetimeIndex(days).year == year)]
        if not len(test_days):
            continue
        cut = days[max(0, np.searchsorted(days, test_days[0]) - H - 1)]          # purge labels that run into the test year
        tr = d[(d.date < cut) & d[f"{y}x"].notna()]
        rng = np.random.default_rng(year * 10 + H)
        si = rng.choice(len(tr), size=min(len(tr), 400000), replace=False)
        gb = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=1000, subsample=0.8,
                               subsample_freq=1, colsample_bytree=0.8, verbose=-1).fit(tr[fs].values[si], tr[f"{y}x"].values[si])
        t2 = tr.iloc[si[:200000]]
        rk = t2.groupby("date")[fs].rank(pct=True) - 0.5
        yr = t2.groupby("date")[f"{y}x"].rank(pct=True) - 0.5
        sign = {f: float(np.sign((rk[f] * yr).mean())) for f in fs}
        if H in (10, 20):                                    # rules on every test day: for rebalance-day robustness
            al = d[d.date.isin(test_days)][["date", "symbol", y] + fs].copy()
            rka = al.groupby("date")[fs].rank(pct=True) - 0.5
            al["rules"] = sum(sign[f] * rka[f].fillna(0).values for f in fs if sign[f])
            al[["date", "symbol", y, "rules"]].rename(columns={y: "fwd"}).to_parquet(OUT / f"rules_all_{year}_{H}.parquet", index=False)
        reb = test_days[::H]
        te = d[d.date.isin(reb)].copy()
        te["boosting"] = gb.predict(te[fs].values)
        rk = te.groupby("date")[fs].rank(pct=True) - 0.5
        te["rules"] = sum(sign[f] * rk[f].fillna(0).values for f in fs if sign[f])
        te["momentum"] = te.r252_21.fillna(-9)
        te["random"] = rng.random(len(te))
        te["H"] = H
        out.append(te[["date", "symbol", "H", y, "boosting", "rules", "momentum", "random"]].rename(columns={y: "fwd"}))
        imp = sorted(zip(fs, gb.feature_importances_.tolist()), key=lambda x: -x[1])[:10]
        (OUT / f"meta_{year}_{H}.json").write_text(json.dumps(dict(signs=sign, boosting_top=imp)))
    pd.concat(out).to_parquet(OUT / f"scores_{year}.parquet", index=False)
    log(f"fold {year} done")


def stats(r, per_year):
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / per_year
    return dict(periods=len(r), cagr=round(100 * (eq.iloc[-1] ** (1 / yrs) - 1), 1) if eq.iloc[-1] > 0 else -100.0,
                sharpe=round(float(r.mean() / r.std() * np.sqrt(per_year)), 2) if r.std() > 0 else None,
                max_dd=round(100 * float((eq / eq.cummax() - 1).min()), 1), win=round(100 * float((r > 0).mean()), 1))


def report():
    s = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob("scores_*.parquet"))])
    nf = pd.read_parquet(OUT / "nifty.parquet")
    nf["date"] = pd.to_datetime(nf.date)
    nf = nf.set_index("date").nifty.sort_index()
    rows, years = [], []
    for H in HS:
        x = s[s.H == H]
        reb = sorted(x.date.unique())
        idx = nf.index
        nret = pd.Series({t: (nf.iloc[min(len(idx) - 1, idx.searchsorted(t) + 1 + H)] / nf.iloc[min(len(idx) - 1, idx.searchsorted(t) + 1)] - 1) for t in reb})
        per_year = 248 / H
        rows.append(dict(H=H, method="Nifty", N=0, **stats(nret, per_year)))
        for m in ("boosting", "rules", "momentum", "random"):
            for N in NS:
                prev, rets, ex = set(), [], []
                for t in reb:
                    g = x[(x.date == t) & x.fwd.notna()]
                    top = g.nlargest(N, m)
                    names = set(top.symbol)
                    turn = (len(names - prev) + len(prev - names)) / N if prev else 1.0
                    r = float(top.fwd.mean()) - SIDE * turn - (SIDE if t == reb[-1] else 0)
                    rets.append(r)
                    ex.append(r - nret.get(t, np.nan))
                    prev = names
                rr = pd.Series(rets, index=reb)
                exs = pd.Series(ex, index=reb)
                yb = exs.groupby(pd.DatetimeIndex(exs.index).year).sum()
                st = stats(rr, per_year)
                rows.append(dict(H=H, method=m, N=N, **st, beat_nifty_years=f"{int((yb > 0).sum())}/{len(yb)}",
                                 excess_per_year=round(100 * float(exs.mean() * per_year), 1)))
                for yv, v in yb.items():
                    years.append(dict(H=H, method=m, N=N, year=int(yv), excess_vs_nifty=round(100 * float(v), 1)))
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "report.csv", index=False)
    pd.DataFrame(years).to_csv(OUT / "by_year.csv", index=False)
    pd.set_option("display.width", 250)
    print(r.to_string(index=False))
    y = pd.DataFrame(years)
    print("\nexcess vs Nifty by year, top 10 (percentage points):")
    print(y[y.N == 10].pivot_table(index="year", columns=["H", "method"], values="excess_vs_nifty").round(1).to_string())
    meta = {f.stem: json.loads(f.read_text()) for f in OUT.glob("meta_2026_*.json")}
    for k, v in sorted(meta.items()):
        print(k, "boosting top:", v["boosting_top"][:8])
        print("   rules signs:", {a: b for a, b in v["signs"].items() if b})


def robust():
    """Simple-rules swing book under variations: start day, number of stocks, costs, and a Nifty 200-day filter."""
    nf = pd.read_parquet(OUT / "nifty.parquet")
    nf["date"] = pd.to_datetime(nf.date)
    nf = nf.set_index("date").nifty.sort_index()
    above = nf > nf.rolling(200, min_periods=150).mean()
    idx = nf.index
    rows = []
    for H in (10, 20):
        a = pd.concat([pd.read_parquet(f) for f in sorted(OUT.glob(f"rules_all_*_{H}.parquet"))])
        days = sorted(a.date.unique())
        by = {t: g for t, g in a.groupby("date")}
        for off in range(0, H, max(1, H // 5)):
            reb = days[off::H]
            nret = pd.Series({t: nf.iloc[min(len(idx) - 1, idx.searchsorted(t) + 1 + H)] / nf.iloc[min(len(idx) - 1, idx.searchsorted(t) + 1)] - 1 for t in reb})
            for N in (10, 15, 20):
                for side in (0.0025, 0.0040):
                    for filt in (False, True):
                        prev, rets = set(), []
                        for t in reb:
                            g = by[t]
                            g = g[g.fwd.notna()]
                            if filt and not bool(above.asof(t)):
                                names = set()
                                r = 0.0
                            else:
                                top = g.nlargest(N, "rules")
                                names = set(top.symbol)
                                r = float(top.fwd.mean())
                            k = max(N, 1)
                            turn = (len(names - prev) + len(prev - names)) / k
                            rets.append(r - side * turn)
                            prev = names
                        rr = pd.Series(rets, index=reb)
                        ex = (rr - nret).groupby(pd.DatetimeIndex(reb).year).sum()
                        st = stats(rr, 248 / H)
                        rows.append(dict(H=H, offset=off, N=N, cost_side=side, nifty_filter=filt, **st,
                                         nifty_cagr=stats(nret, 248 / H)["cagr"], beat_years=f"{int((ex > 0).sum())}/{len(ex)}",
                                         y2025=round(100 * float(ex.get(2025, np.nan)), 1)))
        log(f"robust H={H} done")
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "robust.csv", index=False)
    pd.set_option("display.width", 250)
    g = r.groupby(["H", "N", "cost_side", "nifty_filter"]).agg(cagr_min=("cagr", "min"), cagr_avg=("cagr", "mean"), cagr_max=("cagr", "max"),
                                                              dd_avg=("max_dd", "mean"), dd_worst=("max_dd", "min"), sharpe_avg=("sharpe", "mean"),
                                                              nifty=("nifty_cagr", "mean"), beat=("beat_years", lambda v: ",".join(sorted(set(v)))),
                                                              y2025=("y2025", "mean")).round(1)
    print(g.to_string())


if __name__ == "__main__":
    kw = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    part = kw.get("part", "report")
    if part == "prep":
        prep()
    elif part == "robust":
        robust()
    elif part.startswith("fold:"):
        fold(int(part.split(":")[1]))
    else:
        report()

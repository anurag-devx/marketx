"""Intraday research (separate from swing): pick the 3-5 best setups each morning, long or short, with a hard stop-loss and
a target, using the first 15 or 30 minutes of 5-minute bars plus the previous night's information.

Entry: the close of the 09:25 bar (09:30) or of the 09:40 bar (09:45). Exit, whichever comes first: target, stop-loss
(if one bar touches both, the stop is assumed: conservative), or the 15:15 square-off. Costs per trade: 0.06% charges
+ 0.05% slippage each side; a stop exit pays another 0.05%. Only the liquid 200 (previous day's 20-day traded value).
Features at the entry time (nothing later): opening gap, move since the open, last-bar move, position in the opening
range, opening-range size vs the usual day range, distance from VWAP, opening volume vs its 20-day norm, previous day's
move and close position, and the market's (all stocks') move since the open; plus last night's panel features
(1-day/5-day move, volatility, volume, sector, Nifty, VIX, results, news tone, filings).
Not available yet (no long history): GIFT Nifty, FII/DII flows, F&O open interest (1 year only), bulk/block deals.
Scorers, walk-forward by year: boosting classifiers (chance that a long / a short reaches +1% before -0.5%), and
baselines: momentum (follow the move since the open), gap fade, random. The day is skipped when no setup clears the
bar learned on the training years.
Run in parts:  python -m research.intraday part=build:0 ... build:4 | part=fold:2019 ... | part=report"""
import json, sys, time
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
D5 = BASE / "data" / "intraday" / "5min"
OUT = BASE / "results" / "intraday"
ENTRIES = {2: "0930", 5: "0945"}
EXIT_SLOT = 71                         # 15:10 bar closes 15:15
GRID = [(0.005, 0.010), (0.005, 0.015), (0.007, 0.010), (0.007, 0.015), (0.010, 0.015), (0.010, 0.020), (0.005, 0.020)]
COST, STOP_EXTRA = 0.0006 + 2 * 0.0005, 0.0005
REF = (0.005, 0.010)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def gname(sl, tp, d):
    return f"{d}_sl{int(sl * 1000)}_tp{int(tp * 1000)}"


def day_arrays(f):
    b = pd.read_parquet(f)
    b["ts"] = pd.to_datetime(b.ts)
    slot = ((b.ts.dt.hour * 60 + b.ts.dt.minute) - 555) // 5
    b = b[(slot >= 0) & (slot <= 74)].assign(slot=slot)
    days = pd.DatetimeIndex(sorted(b.ts.dt.normalize().unique()))
    di = days.get_indexer(b.ts.dt.normalize())
    A = {}
    for k in ("open", "high", "low", "close", "volume"):
        m = np.full((len(days), 75), np.nan, dtype="float64")
        m[di, b.slot.values] = b[k].values
        A[k] = m
    c = pd.DataFrame(A["close"]).ffill(axis=1).values                 # fill gaps with the last close, zero volume
    for k in ("open", "high", "low"):
        A[k] = np.where(np.isnan(A[k]), c, A[k])
    A["close"], A["volume"] = c, np.nan_to_num(A["volume"])
    return days, A


def first_hit(mask):
    any_ = mask.any(1)
    return np.where(any_, mask.argmax(1), 10 ** 6)


def symbol_rows(f):
    days, A = day_arrays(f)
    if len(days) < 60:
        return None
    o, h, l, c, v = A["open"], A["high"], A["low"], A["close"], A["volume"]
    ok_day = ~np.isnan(o[:, 0]) & ~np.isnan(c[:, EXIT_SLOT])
    pc = np.r_[np.nan, c[:-1, 74]]                                      # previous day's last close
    ppc = np.r_[np.nan, np.nan, c[:-2, 74]]
    day_rng = (np.nanmax(h, 1) - np.nanmin(l, 1)) / c[:, 74]
    rng20 = pd.Series(day_rng).shift(1).rolling(20, min_periods=10).mean().values
    pos_prev = np.r_[np.nan, ((c[:, 74] - np.nanmin(l, 1)) / (np.nanmax(h, 1) - np.nanmin(l, 1) + 1e-9))[:-1]]
    tp_ = (h + l + c) / 3
    out = []
    for e, lab in ENTRIES.items():
        ce = c[:, e]
        vol_e = v[:, :e + 1].sum(1)
        vol20 = pd.Series(vol_e).shift(1).rolling(20, min_periods=10).mean().values
        orh, orl = np.nanmax(h[:, :e + 1], 1), np.nanmin(l[:, :e + 1], 1)
        vw = (tp_[:, :e + 1] * v[:, :e + 1]).sum(1) / np.where(vol_e > 0, vol_e, np.nan)
        F = dict(gap=o[:, 0] / pc - 1, r_open=ce / o[:, 0] - 1, r_last=ce / c[:, e - 1] - 1,
                 or_pos=(ce - orl) / (orh - orl + 1e-9), or_rng=(orh - orl) / ce / rng20, vwap_dev=ce / vw - 1,
                 vol_or=np.log((vol_e + 1) / (vol20 + 1)), prev_ret=pc / ppc - 1, prev_pos=pos_prev, rng20=rng20)
        H, L, Cx = h[:, e + 1:EXIT_SLOT + 1], l[:, e + 1:EXIT_SLOT + 1], c[:, EXIT_SLOT]
        R = {"long_time": Cx / ce - 1, "short_time": 1 - Cx / ce}
        for sl, tp in GRID:
            for d in ("long", "short"):
                if d == "long":
                    t_hit, s_hit = first_hit(H >= ce[:, None] * (1 + tp)), first_hit(L <= ce[:, None] * (1 - sl))
                    tret = Cx / ce - 1
                else:
                    t_hit, s_hit = first_hit(L <= ce[:, None] * (1 - tp)), first_hit(H >= ce[:, None] * (1 + sl))
                    tret = 1 - Cx / ce
                r = np.where(s_hit <= t_hit, np.where(s_hit < 10 ** 6, -sl - STOP_EXTRA, tret), tp)
                r = np.where((s_hit == 10 ** 6) & (t_hit == 10 ** 6), tret, r)
                R[gname(sl, tp, d)] = r
        df = pd.DataFrame({**F, **R})
        df["symbol"], df["date"], df["entry"] = f.stem, days, lab
        df = df[ok_day & np.isfinite(df.gap) & (df.gap.abs() < 0.2) & np.isfinite(df.rng20)]
        out.append(df)
    return pd.concat(out)


def build(batch, size=100):
    fs = sorted(D5.glob("*.parquet"))[batch * size:(batch + 1) * size]
    rows = [r for r in (symbol_rows(f) for f in fs) if r is not None]
    if not rows:
        return
    d = pd.concat(rows, ignore_index=True)
    num = [c for c in d.columns if c not in ("symbol", "date", "entry")]
    d[num] = d[num].astype("float32")
    OUT.mkdir(parents=True, exist_ok=True)
    d.to_parquet(OUT / f"bars_{batch}.parquet", index=False)
    log(f"build {batch}: {len(fs)} stocks, {len(d):,} rows")


PANEL = ["f_ret1", "f_ret5", "f_rv", "f_vol", "f_vtr", "s_r1", "m_r1", "m_vix", "m_up", "e_res_s", "e_res_e", "n_tone", "n_cnt", "a_mat"]


def load(entry):
    d = pd.concat([pd.read_parquet(f, filters=[("entry", "==", entry)]) for f in sorted(OUT.glob("bars_*.parquet"))], ignore_index=True)
    d["date"] = pd.to_datetime(d.date)
    # last night's panel features and liquidity (the session before the trading day)
    p = pd.read_parquet(BASE / "data" / "marketlab" / "panel.parquet", columns=["symbol", "date", "next_date"] + PANEL)
    p = p.dropna(subset=["next_date"]).rename(columns={"date": "prev_date", "next_date": "date"})
    p["date"] = pd.to_datetime(p.date)
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "close", "volume"])
    px["date"] = pd.to_datetime(px.date)
    px = px.sort_values(["symbol", "date"])
    px["tv"] = (px.close * px.volume).groupby(px.symbol).transform(lambda s: s.rolling(20, min_periods=10).mean())
    px["liq"] = px.groupby("date").tv.rank(ascending=False)
    p = p.merge(px[["symbol", "date", "liq"]].rename(columns={"date": "prev_date"}), on=["symbol", "prev_date"], how="left")
    d = d.merge(p.drop(columns="prev_date"), on=["symbol", "date"], how="left")
    d = d[d.liq <= 200].copy()
    d["mkt_open"] = d.groupby("date").r_open.transform("median")
    d["rel_open"] = d.r_open - d.mkt_open
    return d


FEATS = ["gap", "r_open", "r_last", "or_pos", "or_rng", "vwap_dev", "vol_or", "prev_ret", "prev_pos", "rng20", "mkt_open", "rel_open"] + PANEL


def fold(year):
    import lightgbm as lgb
    for entry in ENTRIES.values():
        d = load(entry)
        tr, te = d[d.date.dt.year < year], d[d.date.dt.year == year].copy()
        if len(te) == 0 or len(tr) < 50000:
            continue
        ref_l, ref_s = gname(*REF, "long"), gname(*REF, "short")
        rng = np.random.default_rng(year)
        si = rng.choice(len(tr), size=min(len(tr), 500000), replace=False)
        X = tr[FEATS].values[si]
        sc = {}
        for dname, col in (("long", ref_l), ("short", ref_s)):
            y = (tr[col].values[si] > 0).astype(int)
            m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=1000, subsample=0.8,
                                   subsample_freq=1, colsample_bytree=0.8, verbose=-1).fit(X, y)
            te[f"p_{dname}"] = m.predict_proba(te[FEATS].values)[:, 1]
            ptr = m.predict_proba(X)[:, 1]
            sc[dname] = dict(base=float(y.mean()), q90=float(np.quantile(ptr, 0.90)), q97=float(np.quantile(ptr, 0.97)),
                             top=sorted(zip(FEATS, m.feature_importances_.tolist()), key=lambda x: -x[1])[:8])
        te["dir_ml"] = np.where(te.p_long - sc["long"]["base"] >= te.p_short - sc["short"]["base"], "long", "short")
        te["score_ml"] = np.maximum(te.p_long - sc["long"]["base"], te.p_short - sc["short"]["base"])
        te["bar90"] = np.where(te.dir_ml == "long", te.p_long >= sc["long"]["q90"], te.p_short >= sc["short"]["q90"])
        te["bar97"] = np.where(te.dir_ml == "long", te.p_long >= sc["long"]["q97"], te.p_short >= sc["short"]["q97"])
        te["rand"] = rng.random(len(te))
        keep = ["date", "symbol", "dir_ml", "score_ml", "bar90", "bar97", "rand", "r_open", "rel_open", "gap", "p_long", "p_short"] + \
               [c for c in te.columns if c.startswith(("long_", "short_"))]
        te[keep].to_parquet(OUT / f"pred_{entry}_{year}.parquet", index=False)
        (OUT / f"meta_{entry}_{year}.json").write_text(json.dumps(sc))
        log(f"fold {year} {entry}: {te.date.nunique()} days, train base long {sc['long']['base']:.3f} short {sc['short']['base']:.3f}")


def pick(g, method, k):
    """The k trades of one day for a method: (direction, rows). Returns a DataFrame with 'dir'."""
    if method.startswith("ml"):
        bar = {"ml": None, "ml_bar90": "bar90", "ml_bar97": "bar97"}[method]
        x = g[g[bar]] if bar else g
        x = x.nlargest(k, "score_ml")
        return x.assign(dir=x.dir_ml)
    if method == "momentum":
        a = g.nlargest((k + 1) // 2, "rel_open").assign(dir="long")
        b = g.nsmallest(k // 2, "rel_open").assign(dir="short")
        return pd.concat([a, b])
    if method == "gap_fade":
        a = g.nsmallest((k + 1) // 2, "gap").assign(dir="long")
        b = g.nlargest(k // 2, "gap").assign(dir="short")
        return pd.concat([a, b])
    x = g.nlargest(k, "rand")
    return x.assign(dir=np.where(x.rand > x.rand.median(), "long", "short"))


def report():
    rows, years = [], []
    rules = ["time"] + [f"sl{int(a * 1000)}_tp{int(b * 1000)}" for a, b in GRID]
    for entry in ENTRIES.values():
        fs = sorted(OUT.glob(f"pred_{entry}_*.parquet"))
        if not fs:
            continue
        d = pd.concat([pd.read_parquet(f) for f in fs])
        alldays = sorted(d.date.unique())
        by = list(d.groupby("date"))
        for method in ("ml", "ml_bar90", "ml_bar97", "momentum", "gap_fade", "random"):
            for k in (3, 5):
                picked = pd.concat([pick(g, method, k) for _, g in by])
                for rule in rules:
                    tr = np.where(picked.dir == "long", picked[f"long_{rule}"], picked[f"short_{rule}"]) - COST
                    t = pd.DataFrame({"date": picked.date.values, "r": tr})
                    day = t.groupby("date").r.sum() / k                       # capital split in k equal slots; unused slots idle
                    day = day.reindex(alldays).fillna(0.0)
                    row = dict(entry=entry, method=method, k=k, exit=rule, trades=len(t), days_traded=int(t.date.nunique()), days=len(alldays),
                               win=round(100 * float((t.r > 0).mean()), 1), avg_win=round(100 * float(t.r[t.r > 0].mean()), 2),
                               avg_loss=round(100 * float(t.r[t.r <= 0].mean()), 2), per_trade=round(100 * float(t.r.mean()), 3))
                    for L in (1, 2, 5):
                        dl = (L * day).clip(lower=-0.99)
                        eq = (1 + dl).cumprod()
                        row[f"day_{L}x"] = round(100 * float(dl.mean()), 3)
                        row[f"maxdd_{L}x"] = round(100 * float((eq / eq.cummax() - 1).min()), 1)
                        row[f"cagr_{L}x"] = round(100 * float(eq.iloc[-1] ** (248 / len(dl)) - 1), 1) if eq.iloc[-1] > 0 else -100.0
                    yr = day.groupby(pd.DatetimeIndex(day.index).year).mean()
                    row["neg_years"] = f"{int((yr < 0).sum())}/{len(yr)}"
                    rows.append(row)
                    for y, v in yr.items():
                        years.append(dict(entry=entry, method=method, k=k, exit=rule, year=int(y), day_1x_pct=round(100 * float(v), 3)))
        log(f"report {entry} done")
    r = pd.DataFrame(rows)
    r.to_csv(OUT / "report.csv", index=False)
    pd.DataFrame(years).to_csv(OUT / "by_year.csv", index=False)
    pd.set_option("display.width", 260)
    cols = ["entry", "method", "k", "exit", "days_traded", "win", "avg_win", "avg_loss", "per_trade", "day_1x", "day_5x", "maxdd_5x", "cagr_1x", "neg_years"]
    print("best 25 by average day (1x):")
    print(r.sort_values("day_1x", ascending=False)[cols].head(25).to_string(index=False))
    print("\nbaselines, best exit each:")
    print(r[r.method.isin(["momentum", "gap_fade", "random"])].sort_values("day_1x", ascending=False).groupby(["entry", "method"]).head(1)[cols].to_string(index=False))
    y = pd.DataFrame(years)
    best = r.sort_values("day_1x", ascending=False).iloc[0]
    yy = y[(y.entry == best.entry) & (y.method == best.method) & (y.k == best.k) & (y.exit == best.exit)]
    print("\nbest setup by year (% per day, 1x):", dict(zip(yy.year, yy.day_1x_pct)))
    for f in sorted(OUT.glob("meta_*_2026.json")):
        m = json.loads(f.read_text())
        print(f.stem, {d: m[d]["top"][:6] for d in m})


if __name__ == "__main__":
    kw = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
    part = kw.get("part", "report")
    if part.startswith("build:"):
        build(int(part.split(":")[1]))
    elif part.startswith("fold:"):
        fold(int(part.split(":")[1]))
    else:
        report()

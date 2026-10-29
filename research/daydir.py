"""Day-direction test (intraday track): can the market's 09:30 -> 15:15 direction be called at 09:15 / 09:30, and does
trading the intraday picks only on the called side help?

Target: average 09:30 -> 15:15 move of the liquid 200 (from results/intraday bars; equal weight).
Inputs, all known before the call:
  overnight  last night's Nifty 1-day / 5-day move, India VIX vs its 1-year median, breadth, Brent / USD-INR / US VIX /
             US 10-year changes (panel x_*), and when present in data/macro: S&P 500, Nasdaq, Nikkei last change (FRED;
             the US close is the night before; Nikkei the same morning is NOT used, only its previous close)
  09:15      the market's opening gap (average gap of the liquid 200)
  09:30      the market's move in the first 15 minutes and how many stocks are up
Not available yet: GIFT Nifty, FII/DII flows (4 days only), pre-open order book.
Models, walk-forward by year: logistic regression (scaled) and small gradient boosting; rules: follow the first 15
minutes, fade the opening gap. Reported: hit rate vs always guessing the common side, and the value for the intraday
book (top-3 learned-model picks, hold to 15:15) when only picks on the called side are taken.
Run:  python -m research.daydir"""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path(__file__).resolve().parents[1]
IN = BASE / "results" / "intraday"
OUT = BASE / "results" / "daydir"
COST = 0.0006 + 2 * 0.0005


def days_frame():
    b = pd.concat([pd.read_parquet(f, columns=["symbol", "date", "entry", "gap", "r_open", "long_time"], filters=[("entry", "==", "0930")])
                   for f in sorted(IN.glob("bars_*.parquet"))])
    b["date"] = pd.to_datetime(b.date)
    px = pd.read_parquet(BASE / "data" / "prices.parquet", columns=["date", "symbol", "close", "volume"])
    px["date"] = pd.to_datetime(px.date)
    px = px.sort_values(["symbol", "date"])
    px["tv"] = (px.close * px.volume).groupby(px.symbol).transform(lambda s: s.rolling(20, min_periods=10).mean())
    px["liq"] = px.groupby("date").tv.rank(ascending=False)
    px["next"] = px.groupby("symbol").date.shift(-1)
    b = b.merge(px[["symbol", "next", "liq"]].rename(columns={"next": "date"}), on=["symbol", "date"], how="left")
    b = b[b.liq <= 200]
    g = b.groupby("date")
    d = pd.DataFrame({"y": g.long_time.mean(), "mkt_gap": g.gap.mean(), "mkt_open": g.r_open.mean(), "up_open": g.r_open.apply(lambda s: (s > 0).mean()),
                      "disp_open": g.r_open.std(), "n": g.size()})
    d = d[d.n >= 150]
    # last night's market features (same for every stock in the panel)
    p = pd.read_parquet(BASE / "data" / "marketlab" / "panel.parquet", columns=["date", "next_date", "m_r1", "m_r5", "m_vix", "m_up", "x_brent", "x_inr", "x_vix", "x_y10"])
    p = p.dropna(subset=["next_date"]).groupby("next_date").first().drop(columns="date")
    p.index = pd.to_datetime(p.index)
    d = d.join(p, how="left")
    # global equity closes from FRED when fetched (data/macro): last change before the Indian session
    try:
        m = pd.read_parquet(BASE / "data" / "macro" / "series.parquet")
        m["date"] = pd.to_datetime(m.date)
        for sid, col in (("SP500", "us_spx"), ("NASDAQCOM", "us_ndx"), ("NIKKEI225", "jp_nky")):
            s = m[m.series == sid].set_index("date").value.sort_index()
            if len(s) > 500:
                ch = s.pct_change()
                d[col] = [ch[ch.index < t].iloc[-1] if (ch.index < t).any() else np.nan for t in d.index]
    except Exception:
        pass
    return d.dropna(subset=["y", "m_r1"])


def walk(d, feats, model):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    import lightgbm as lgb
    out = []
    for yr in range(2019, 2027):
        tr, te = d[d.index.year < yr], d[d.index.year == yr]
        if len(tr) < 300 or not len(te):
            continue
        X, y = tr[feats].fillna(0).values, (tr.y > 0).astype(int).values
        if model == "logistic":
            m = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=500)).fit(X, y)
        else:
            m = lgb.LGBMClassifier(n_estimators=150, learning_rate=0.03, num_leaves=7, min_child_samples=40, verbose=-1).fit(X, y)
        out.append(pd.Series(m.predict_proba(te[feats].fillna(0).values)[:, 1], index=te.index))
    return pd.concat(out) if out else pd.Series(dtype=float)


def main():
    d = days_frame()
    OUT.mkdir(parents=True, exist_ok=True)
    base = ["m_r1", "m_r5", "m_vix", "m_up", "x_brent", "x_inr", "x_vix", "x_y10"] + [c for c in ("us_spx", "us_ndx", "jp_nky") if c in d]
    sets = {"overnight only": base, "+ 09:15 gap": base + ["mkt_gap"], "+ first 15 min": base + ["mkt_gap", "mkt_open", "up_open", "disp_open"]}
    test = d[d.index.year >= 2019]
    rows = [dict(signal="always 'down' (common side)" if (test.y <= 0).mean() >= 0.5 else "always 'up'", hit=round(100 * max((test.y > 0).mean(), (test.y <= 0).mean()), 1), days=len(test))]
    rows.append(dict(signal="rule: follow first 15 min", hit=round(100 * float(((test.mkt_open > 0) == (test.y > 0)).mean()), 1), days=len(test)))
    rows.append(dict(signal="rule: fade opening gap", hit=round(100 * float(((test.mkt_gap < 0) == (test.y > 0)).mean()), 1), days=len(test)))
    preds = {}
    for name, fs in sets.items():
        for model in ("logistic", "boosting"):
            pr = walk(d, fs, model)
            t = d.loc[pr.index]
            hit = ((pr > 0.5) == (t.y > 0))
            conf = (pr - 0.5).abs() >= 0.1
            yr = hit.groupby(hit.index.year).mean()
            rows.append(dict(signal=f"{model}: {name}", hit=round(100 * float(hit.mean()), 1), days=len(hit),
                             confident_hit=round(100 * float(hit[conf].mean()), 1) if conf.sum() else None, confident_days=int(conf.sum()),
                             worst_year=round(100 * float(yr.min()), 1)))
            preds[f"{model}: {name}"] = pr
    r = pd.DataFrame(rows)
    # value for the intraday book: top-3 learned-model picks; only picks on the called side
    vals = []
    pf = sorted(IN.glob("pred_0930_*.parquet"))
    if pf:
        p = pd.concat([pd.read_parquet(f) for f in pf])
        p["date"] = pd.to_datetime(p.date)
        p["r"] = np.where(p.dir_ml == "long", p.long_time, p.short_time) - COST
        days = sorted(p.date.unique())
        def book(sel):
            x = sel.sort_values("score_ml", ascending=False).groupby("date").head(3)
            day = (x.groupby("date").r.sum() / 3).reindex(days).fillna(0)
            yr = day.groupby(pd.DatetimeIndex(day.index).year).mean()
            return dict(day_bps=round(1e4 * float(day.mean()), 1), per_trade_bps=round(1e4 * float(x.r.mean()), 1), trades=len(x),
                        days_traded=int(x.date.nunique()), neg_years=f"{int((yr < 0).sum())}/{len(yr)}")
        vals.append(dict(book="intraday top 3, no direction call", **book(p)))
        best = max(preds, key=lambda k: ((preds[k] > 0.5) == (d.loc[preds[k].index].y > 0)).mean())
        call = preds[best].reindex(p.date.values).values
        side = np.where(call > 0.5, "long", "short")
        vals.append(dict(book=f"only the called side ({best})", **book(p[p.dir_ml.values == side])))
        strong = np.abs(call - 0.5) >= 0.1
        vals.append(dict(book="called side, confident days only", **book(p[(p.dir_ml.values == side) & strong])))
        perfect = np.where(d.y.reindex(p.date.values).values > 0, "long", "short")
        vals.append(dict(book="(ceiling) if the day's direction were known", **book(p[p.dir_ml.values == perfect])))
    v = pd.DataFrame(vals)
    r.to_csv(OUT / "calls.csv", index=False)
    v.to_csv(OUT / "value.csv", index=False)
    pd.set_option("display.width", 220)
    print(f"days {len(d)}, test days (2019+) {len(test)}, share of up days {100 * (test.y > 0).mean():.1f}%; global closes used: {[c for c in ('us_spx', 'us_ndx', 'jp_nky') if c in d]}")
    print(r.to_string(index=False))
    print(v.to_string(index=False))


if __name__ == "__main__":
    main()

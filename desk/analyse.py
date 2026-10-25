"""Stock Desk analysis: everything MarketX computes, for one stock, as of a day (default: the latest), saved as a snapshot
in data/desk/<SYMBOL>/snaps/<day>_<time>.json (+ latest.json). Point in time: every input is cut at the as-of day
(news / filings at 21:00 IST that day), so a past snapshot shows what was known then.

Sections: price · market_lab (frames for the influence map + verdicts for every move / size) · track (Market Lab's past
calls on this stock, from Review) · ta (evidence, patterns, plans) · intraday (crossing profile, opening range, last 60
sessions) · events (reaction to results, material filings, busy news days) · seasonality · fundamentals · ownership · fo ·
peers · relative (1-year strength vs sector index and Nifty) · news · upcoming · reasons (for / against, from the above)."""
import json, time
import numpy as np, pandas as pd
from core import config as C, atomic

DIR = C.DATA / "desk"
SIZES = (0.5, 1.0, 2.0, 3.0)
MOVES = ("cc", "oc", "gp")


def _f(x, d=4):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else round(x, d)


def _jsonable(o):
    if isinstance(o, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(o))[:19]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        return _f(o, 6)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def records(df):
    return json.loads(df.to_json(orient="records", date_format="iso", default_handler=str)) if df is not None and len(df) else []


class Ctx:
    """Inputs loaded once for several stocks."""
    def __init__(self, log=print):
        from core.data import load_raw
        from marketlab import radar as R
        self.log = log
        self.px, self.idx, self.uni = load_raw()
        self.px = self.px.sort_values(["symbol", "date"])
        self.days = np.sort(self.px.date.unique())
        self.panel = R.load_panel()
        rd = lambda p, cols=None: (pd.read_parquet(p, columns=cols) if p.exists() else None)
        self.news = rd(C.DATA / "news" / "news.parquet", ["published", "title", "source", "link", "symbols", "tone", "event_type"])
        self.ann = rd(C.DATA / "announcements.parquet", ["id", "published", "symbol", "subject", "category", "link"])
        self.hold = rd(C.DATA / "shareholding" / "holdings.parquet")
        self.fo = rd(C.DATA / "derivatives" / "fo_daily.parquet")
        self.cal = rd(C.DATA / "fundamentals" / "calendar.parquet")
        try:
            from core.indices import wide
            self.ix = wide("close")
        except Exception:
            self.ix = pd.DataFrame()
        try:
            from research.earnings import quarters
            self.q = quarters()
        except Exception:
            self.q = None
        self._lib = {}

    def library(self, day):
        from marketlab import engine as E
        if day not in self._lib:
            d = self.panel
            lib = d[(d.next_date <= day) & d.cc.notna()]
            self.log(f"Matching against {len(lib):,} past stock-days (as of {day:%d %b %Y}) ...")
            self._lib = {day: E.Library(lib)}
        return self._lib[day]

    def bars(self, sym, day, n=None):
        g = self.px[(self.px.symbol == sym) & (self.px.date <= day)]
        return g.tail(n) if n else g


# ---------------- sections ----------------
def price_block(g):
    c = g.close.astype(float)
    r = lambda k: _f(c.iat[-1] / c.iat[-1 - k] - 1) if len(c) > k else None
    y = g.tail(250)
    return dict(close=_f(c.iat[-1], 2), change=r(1), r1m=r(21), r3m=r(63), r1y=r(250), hi52=_f(y.high.max(), 2), lo52=_f(y.low.min(), 2),
                date=str(pd.Timestamp(g.date.iat[-1]).date()))


def market_lab(ctx, sym, day):
    from marketlab.radar import N_CASES
    from web.r_marketlab import _verdict, _skill
    d = ctx.panel
    if d is None:
        return dict(ready=False, why="No scenario panel yet: build the radar once.")
    q = d[(d.date == day) & (d.symbol == sym)].reset_index(drop=True)
    if q.empty:
        return dict(ready=False, why=f"{sym} has no scenario on {day:%d %b %Y} (not in the panel: rebuild the radar, or not a Nifty 500 stock).")
    L = ctx.library(day)
    est, idx, dist = L.estimate(q)
    from marketlab.features import FEATURES
    row = pd.concat([q[["symbol", "date", "sector", "close"] + [c for c in q if c.startswith("raw_")] + FEATURES], est], axis=1)
    drv = L.drivers(q, est)
    cases = pd.DataFrame([(sym, r + 1, L.meta.symbol.iat[j], L.meta.date.iat[j], float(dist[0, r]), float(L.out["cc"][j]), float(L.out["oc"][j]), float(L.out["gp"][j]))
                          for r, j in enumerate(idx[0][:N_CASES])], columns=["symbol", "rank", "case_symbol", "case_date", "distance", "cc", "oc", "gp"])
    sp = []
    for m in MOVES:
        y = L.outcomes(q, idx, m)[0] * 100
        sp.append((sym, m, *[float(x) for x in np.percentile(y, [10, 25, 50, 75, 90])], float((y > 0).mean())))
    spread = pd.DataFrame(sp, columns=["symbol", "move", "p10", "p25", "p50", "p75", "p90", "up_share"])
    r = row.iloc[0]
    verdicts = []
    for m in MOVES:
        for s in SIZES:
            sk, _ = _skill(m, s)
            v = _verdict(r, m, s, sk)
            verdicts.append(dict(move=m, size=s, **{k: v[k] for k in ("chance", "usual", "lift", "lean", "confidence", "up_share", "n_big")}))
    return dict(ready=True, frames=dict(radar=records(row), drivers=records(drv), cases=records(cases), spread=records(spread)), verdicts=verdicts,
                dist_pct=_f(r.dist_pct), day_pct=_f(r.get("day_pct")))


def track(sym):
    from marketlab import review as RV
    c = RV.load("all", "calls")
    if c.empty:
        return dict(n=0)
    c = c[(c.symbol == sym)].dropna(subset=["ret"])
    out = []
    for (m, s), g in c.groupby(["move", "size"]):
        lr = g.lean_right.dropna()
        out.append(dict(move=m, size=s, n=int(len(g)), hit=_f(g.hit.mean()), said=_f(g.chance.mean()), usual=_f(g.usual.mean()),
                        leans=int(len(lr)), lean_right=_f(lr.mean()) if len(lr) else None, picks=int(g.pick.sum())))
    return dict(n=int(c.day.nunique()), rows=out)


def ta(ctx, sym, day, ml):
    from tradeplan import plan as TP, patterns as PT, evidence as EV
    g = ctx.bars(sym, day, 260).reset_index(drop=True)
    if len(g) < 30:
        return dict(ready=False, why="not enough price history")
    pats, src = PT.recent(g, days=5)
    plans = None
    if ml.get("ready"):
        rd = pd.DataFrame(ml["frames"]["radar"])
        sp = pd.DataFrame(ml["frames"]["spread"])
        try:
            plans = TP.build(str(day.date()), rd, sp, ctx.px).get("plans", {}).get(sym)
        except Exception as e:
            ctx.log(f"  plan skipped: {type(e).__name__}")
    lv = TP.levels(g)
    ev = EV.evidence(g, pats, lv)
    return dict(ready=True, evidence=ev, patterns=pats, patterns_source=src, levels=lv, plans=plans, candles=PT.describe_candles(g.tail(30)),
                bars=dict(t=g.date.dt.strftime("%Y-%m-%d").tolist()[-150:], o=g.open.round(2).tolist()[-150:], h=g.high.round(2).tolist()[-150:],
                          l=g.low.round(2).tolist()[-150:], c=g.close.round(2).tolist()[-150:], v=g.volume.astype(float).tolist()[-150:]))


def intraday(sym, day, g):
    from marketlab import review as RV
    from core import intraday as I
    b = I.load(sym)
    if b.empty:
        return dict(ready=False, why="no 5-minute bars for this stock")
    b = b.assign(ts=pd.to_datetime(b.ts))
    t = b.ts.dt.strftime("%H:%M")
    b = b[(t >= "09:15") & (t < "15:30")].assign(d=lambda x: x.ts.dt.normalize())
    b = b[b.d <= pd.Timestamp(day)]
    sess = sorted(b.d.unique())[-60:]
    if not sess:
        return dict(ready=False, why="no sessions up to that day")
    closes = g.set_index("date").close
    rows = []
    for s in sess:
        x = b[b.d == s].reset_index(drop=True)
        prev = closes[closes.index < s]
        if x.empty or prev.empty:
            continue
        p = RV.profile(x, float(prev.iat[-1]), 1.0)
        o30 = x[x.ts < s + pd.Timedelta(minutes=15 + 15 + 9 * 60)]
        hi, lo = x.high.max(), x.low.min()
        rows.append(dict(day=str(pd.Timestamp(s).date()), n_cross=p["n_cross"], minutes=p["minutes_beyond"], first=p["first_cross"], held=p["held_to_close"],
                         best=_f(p["best_pct"], 2), worst=_f(p["worst_pct"], 2), close=_f(p["close_pct"], 2),
                         or_pct=_f(100 * (o30.high.max() / o30.low.min() - 1), 2) if len(o30) else None,
                         extreme_in_or=bool(len(o30) and (o30.high.max() >= hi or o30.low.min() <= lo))))
    f = pd.DataFrame(rows)
    if f.empty:
        return dict(ready=False, why="no usable sessions")
    crossed = f[f.n_cross > 0]
    fc = pd.to_datetime(crossed["first"], format="%H:%M", errors="coerce")
    return dict(ready=True, sessions=len(f), crossed_pct=_f((f.n_cross > 0).mean()), avg_cross=_f(crossed.n_cross.mean(), 2) if len(crossed) else None,
                median_minutes=_f(crossed.minutes.median(), 0) if len(crossed) else None,
                median_first=fc.median().strftime("%H:%M") if len(fc.dropna()) else None, held_pct=_f(crossed.held.mean()) if len(crossed) else None,
                choppy_pct=_f((crossed.n_cross >= 3).mean()) if len(crossed) else None, or_median=_f(f.or_pct.median(), 2),
                extreme_in_or_pct=_f(f.extreme_in_or.mean()), last=records(f.tail(20)))


def events(ctx, sym, day, g):
    from marketlab.features import decision_day, MATERIAL
    cut = pd.Timestamp(day) + pd.Timedelta(hours=21)
    c = g.set_index("date").close.astype(float)
    dates = c.index

    def react(ts):
        dd = decision_day(pd.to_datetime(pd.Series(ts)), ctx.days)
        out = []
        for t, d0 in zip(ts, dd):
            if pd.isna(d0) or d0 not in dates:
                continue
            i = dates.get_loc(d0)
            if i + 1 >= len(dates):
                continue
            r1 = c.iat[i + 1] / c.iat[i] - 1
            r5 = c.iat[min(i + 5, len(c) - 1)] / c.iat[i] - 1
            out.append(dict(day=str(pd.Timestamp(d0).date()), r1=_f(r1), r5=_f(r5)))
        return out

    def summ(rows):
        if not rows:
            return dict(n=0)
        f = pd.DataFrame(rows).dropna()
        return dict(n=len(f), avg_abs1=_f(f.r1.abs().mean()), big1=_f((f.r1.abs() >= 0.01).mean()), up1=_f((f.r1 > 0).mean()), avg5=_f(f.r5.mean()),
                    last=rows[-8:])
    out = {}
    a = ctx.ann
    if a is not None:
        a = a[(a.symbol == sym) & (pd.to_datetime(a.published) <= cut)]
        res = a[a.category == "Results"].drop_duplicates(subset=None).assign(p=lambda x: pd.to_datetime(x.published))
        res = res.sort_values("p").groupby(res.p.dt.to_period("Q")).head(1)
        out["results"] = summ(react(res.p.tail(16).tolist()))
        mat = a[a.category.isin(MATERIAL)].assign(p=lambda x: pd.to_datetime(x.published)).sort_values("p")
        out["material"] = summ(react(mat.p.tail(30).tolist()))
    n = ctx.news
    if n is not None:
        n = n[n.symbols.astype(str).str.contains(rf"(?:^|,)\s*{sym}\s*(?:,|$)", regex=True) & (pd.to_datetime(n.published) <= cut)]
        dd = decision_day(pd.to_datetime(n.published), ctx.days)
        busy = pd.Series(1, index=dd).groupby(level=0).sum()
        busy = busy[busy >= 3]
        out["busy_news"] = summ(react([pd.Timestamp(x) for x in busy.index[-20:]]))
    base = c.pct_change().dropna().tail(750)
    out["normal"] = dict(avg_abs1=_f(base.abs().mean()), big1=_f((base.abs() >= 0.01).mean()), up1=_f((base > 0).mean()))
    return out


def seasonality(g):
    c = g.set_index("date").close.astype(float)
    r = c.pct_change().dropna()
    r = r[r.index >= r.index.max() - pd.Timedelta(days=3 * 365)]
    wd = r.groupby(r.index.dayofweek).agg(["mean", "count", lambda x: (x.abs() >= 0.01).mean(), lambda x: (x > 0).mean()])
    wd.columns = ["mean", "n", "big", "up"]
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    m = c.resample("ME").last().pct_change().dropna()
    m = m[m.index >= m.index.max() - pd.Timedelta(days=5 * 365)]
    mm = m.groupby(m.index.month).agg(["mean", "count", lambda x: (x > 0).mean()])
    mm.columns = ["mean", "n", "up"]
    mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return dict(weekday=[dict(day=names[i], mean=_f(x["mean"]), big=_f(x.big), up=_f(x.up), n=int(x.n)) for i, x in wd.iterrows()],
                month=[dict(month=mon[i - 1], mean=_f(x["mean"]), up=_f(x.up), n=int(x.n)) for i, x in mm.iterrows()])


def fundamentals(ctx, sym, day, price):
    out = dict(val=None, quarters=[])
    try:
        from research import valuation as VA
        out["val"] = {k: (v if not isinstance(v, float) else _f(v, 3)) for k, v in VA.valuation(sym, price, day).items()}
    except Exception:
        pass
    q = ctx.q
    if q is not None and len(q):
        x = q[(q.symbol == sym) & (pd.to_datetime(q.filed_at) <= pd.Timestamp(day) + pd.Timedelta(hours=21))].sort_values("period_end").tail(8)
        cols = [c for c in ("period_end", "filed_at", "basis", "revenue_cr", "profit_cr", "margin_pct", "revenue_yoy", "profit_yoy", "sue_profit") if c in x]
        out["quarters"] = records(x[cols])
    return out


def ownership(ctx, sym, day):
    h = ctx.hold
    if h is None:
        return []
    h = h[(h.symbol == sym) & (pd.to_datetime(h.published) <= pd.Timestamp(day) + pd.Timedelta(hours=21))].sort_values("period_end").tail(8)
    return records(h[[c for c in ("period_end", "published", "promoter_pct", "fii_pct", "dii_pct", "mf_pct", "retail_pct", "holders") if c in h]])


def fo(ctx, sym, day):
    f = ctx.fo
    if f is None:
        return dict(ready=False)
    f = f[(f.symbol == sym) & (pd.to_datetime(f.date) <= pd.Timestamp(day))].sort_values("date").tail(20)
    if f.empty:
        return dict(ready=False, why="not an F&O stock (or no F&O data yet)")
    last = f.iloc[-1]
    return dict(ready=True, last=dict(date=str(pd.Timestamp(last.date).date()), fut_oi=_f(last.fut_oi, 0), oi_chg=_f(last.get("fut_oi_chg"), 0), pcr=_f(last.get("pcr"), 2),
                                      basis_pct=_f(last.get("basis_pct"), 3), expiry=str(last.get("fut_expiry"))[:10]),
                rows=records(f[[c for c in ("date", "fut_oi", "fut_oi_chg", "pcr", "basis_pct", "underlying") if c in f]]))


def peers(ctx, sym, day, k=5):
    from tradeplan import evidence as EV
    col = "Symbol" if "Symbol" in ctx.uni else "symbol"
    sec = dict(zip(ctx.uni[col].astype(str), ctx.uni.get("Industry", pd.Series("", index=ctx.uni.index)).astype(str)))
    mine = sec.get(sym, "")
    px = ctx.px[(ctx.px.date <= day) & (ctx.px.date > pd.Timestamp(day) - pd.Timedelta(days=100))]
    tv = (px.close * px.volume).groupby(px.symbol).median()
    same = [s for s, v in sec.items() if v == mine and s != sym and s in tv.index]
    if sym not in tv.index or not same:
        return dict(sector=mine, rows=[])
    dist = (np.log(tv[same]) - np.log(tv[sym])).abs().sort_values()
    pick = [sym] + list(dist.index[:k])
    try:
        from marketlab import radar as R
        rd = R.read("radar") if R.latest_day() == str(pd.Timestamp(day).date()) else pd.DataFrame()
    except Exception:
        rd = pd.DataFrame()
    rows = []
    for s in pick:
        g = ctx.bars(s, day, 260).reset_index(drop=True)
        if len(g) < 30:
            continue
        pb = price_block(g)
        ev = EV.evidence(g)
        r = rd[rd.symbol == s] if len(rd) else rd
        try:
            from research import valuation as VA
            v = VA.valuation(s, pb["close"], day)
        except Exception:
            v = {}
        rows.append(dict(symbol=s, me=s == sym, close=pb["close"], r1m=pb["r1m"], r3m=pb["r3m"], r1y=pb["r1y"], turnover_cr=_f(tv.get(s, np.nan) / 1e7, 1),
                         ta=ev["dir"], ta_net=ev["net"], pe=_f(v.get("pe"), 1), pb=_f(v.get("pb"), 2), roe=_f(v.get("roe_pct"), 1),
                         chance=_f(r[f"ch_cc_1.0"].iat[0]) if len(r) else None, usual=_f(r[f"us_cc_1.0"].iat[0]) if len(r) else None))
    return dict(sector=mine, rows=rows, by="same NSE industry, closest median traded value (100 days)")


def relative(ctx, sym, day, peer_syms):
    from core.indices import SECTOR_INDEX
    col = "Symbol" if "Symbol" in ctx.uni else "symbol"
    sec = dict(zip(ctx.uni[col].astype(str), ctx.uni.get("Industry", pd.Series("", index=ctx.uni.index)).astype(str))).get(sym, "")
    start = pd.Timestamp(day) - pd.Timedelta(days=365)
    me = ctx.px[(ctx.px.symbol == sym) & (ctx.px.date > start) & (ctx.px.date <= day)].set_index("date").close
    if me.empty:
        return None
    out = dict(t=me.index.strftime("%Y-%m-%d").tolist(), stock=(100 * me / me.iat[0]).round(2).tolist())
    nif = ctx.idx.set_index("date").nifty.reindex(me.index).ffill()
    out["nifty"] = (100 * nif / nif.dropna().iat[0]).round(2).tolist() if nif.notna().any() else None
    ix = SECTOR_INDEX.get(sec)
    if ix and len(ctx.ix) and ix in ctx.ix:
        s = ctx.ix[ix].reindex(me.index).ffill()
        out["sector"], out["sector_label"] = (100 * s / s.dropna().iat[0]).round(2).tolist() if s.notna().any() else None, ix.replace("nifty_", "Nifty ").title()
    else:
        p = ctx.px[ctx.px.symbol.isin(peer_syms) & (ctx.px.date > start) & (ctx.px.date <= day)].pivot_table(index="date", columns="symbol", values="close")
        if len(p):
            s = (p / p.iloc[0]).median(axis=1).reindex(me.index).ffill()
            out["sector"], out["sector_label"] = (100 * s).round(2).tolist(), "peers (median)"
    return out


def news(ctx, sym, day, days=30):
    cut, start = pd.Timestamp(day) + pd.Timedelta(hours=21), pd.Timestamp(day) - pd.Timedelta(days=days)
    out = dict(news=[], filings=[])
    n = ctx.news
    if n is not None:
        n = n[n.symbols.astype(str).str.contains(rf"(?:^|,)\s*{sym}\s*(?:,|$)", regex=True)]
        n = n[(pd.to_datetime(n.published) <= cut) & (pd.to_datetime(n.published) >= start)].sort_values("published", ascending=False).head(60)
        out["news"] = records(n[["published", "title", "source", "tone", "event_type", "link"]])
    a = ctx.ann
    if a is not None:
        a = a[(a.symbol == sym) & (a.category != "Routine compliance")]
        a = a[(pd.to_datetime(a.published) <= cut) & (pd.to_datetime(a.published) >= start)].sort_values("published", ascending=False).head(60)
        out["filings"] = records(a[["published", "category", "subject", "link"]])
    tone = pd.Series([x.get("tone") for x in out["news"]]).value_counts().to_dict() if out["news"] else {}
    cats = pd.Series([x.get("category") for x in out["filings"]]).value_counts().to_dict() if out["filings"] else {}
    out["summary"] = dict(news=len(out["news"]), tone=tone, filings=len(out["filings"]), categories=cats)
    return out


def upcoming(ctx, sym, day, fo_block):
    ev = []
    if ctx.cal is not None:
        c = ctx.cal[(ctx.cal.symbol == sym) & (pd.to_datetime(ctx.cal.date) >= pd.Timestamp(day))].sort_values("date").head(5)
        ev += [dict(date=str(pd.Timestamp(x.date).date()), what=str(x.purpose)) for x in c.itertuples()]
    if fo_block.get("ready") and fo_block["last"].get("expiry"):
        ev.append(dict(date=fo_block["last"]["expiry"], what="F&O expiry (current month)"))
    return sorted(ev, key=lambda x: x["date"])


def reasons(snap):
    """For / against, each with its source section, from the computed sections (no new judgement)."""
    pro, con = [], []
    add = lambda sign, text, src, w=1.0: (pro if sign > 0 else con).append(dict(text=text, src=src, w=w)) if sign else None
    ml = snap.get("market_lab") or {}
    if ml.get("ready"):
        v = next((x for x in ml["verdicts"] if x["move"] == "cc" and x["size"] == 1.0), None)
        if v and v["lean"] in ("up", "down"):
            add(1 if v["lean"] == "up" else -1, f"Market Lab leans {v['lean']} (tested direction)", "Market Lab", 3)
        drv = pd.DataFrame(ml["frames"]["drivers"])
        if len(drv):
            d = drv[(drv.move == "cc") & (drv["size"] == 1.0)].assign(w=lambda x: x.d_up.abs()).sort_values("w", ascending=False).head(3)
            for x in d.itertuples():
                if abs(x.d_up) >= 0.03:
                    add(np.sign(x.d_up), f"{x.node} pushes the lean {'up' if x.d_up > 0 else 'down'}", "Market Lab drivers", 1.5)
    t = snap.get("ta") or {}
    if t.get("ready"):
        for c in t["evidence"]["checks"]:
            if c["verdict"]:
                add(c["verdict"], f"{c['id']} {c['reason']}", "Technical", 1)
    f = (snap.get("fundamentals") or {}).get("quarters") or []
    if f and f[-1].get("profit_yoy") is not None:
        py = f[-1]["profit_yoy"]
        add(1 if py > 0.05 else -1 if py < -0.05 else 0, f"profit {py:+.0%} vs a year ago (latest quarter)", "Fundamentals", 1.5)
    o = snap.get("ownership") or []
    if len(o) >= 2 and o[-1].get("fii_pct") is not None and o[-2].get("fii_pct") is not None:
        ch = o[-1]["fii_pct"] - o[-2]["fii_pct"]
        add(1 if ch > 0.25 else -1 if ch < -0.25 else 0, f"FII holding {ch:+.2f} pts last quarter", "Ownership", 1)
    for p in (t.get("patterns") or []):
        if p.get("strength") == "strong" and p["direction"] in ("bullish", "bearish"):
            add(1 if p["direction"] == "bullish" else -1, f"strong {p['label']} on {p['date']}", "Candles", 1)
    key = lambda x: -x["w"]
    return dict(pro=sorted(pro, key=key)[:6], con=sorted(con, key=key)[:6])


# ---------------- run ----------------
def one(ctx, sym, day):
    t0 = time.time()
    g = ctx.bars(sym, day).reset_index(drop=True)
    if g.empty:
        raise ValueError(f"no prices for {sym} up to {day:%d %b %Y}")
    snap = dict(symbol=sym, as_of=str(pd.Timestamp(day).date()), built=str(pd.Timestamp.now().floor("s")))
    col = "Symbol" if "Symbol" in ctx.uni else "symbol"
    u = ctx.uni[ctx.uni[col] == sym]
    snap["name"] = str(u.iloc[0].get("Company Name", sym)) if len(u) else sym
    snap["sector"] = str(u.iloc[0].get("Industry", "")) if len(u) else ""
    snap["price"] = price_block(g)
    steps = [("market_lab", lambda: market_lab(ctx, sym, day)), ("track", lambda: track(sym)),
             ("ta", lambda: ta(ctx, sym, day, snap.get("market_lab") or {})), ("intraday", lambda: intraday(sym, day, g)),
             ("events", lambda: events(ctx, sym, day, g)), ("seasonality", lambda: seasonality(g)),
             ("fundamentals", lambda: fundamentals(ctx, sym, day, snap["price"]["close"])), ("ownership", lambda: ownership(ctx, sym, day)),
             ("fo", lambda: fo(ctx, sym, day)), ("peers", lambda: peers(ctx, sym, day)),
             ("relative", lambda: relative(ctx, sym, day, [r["symbol"] for r in (snap.get("peers") or {}).get("rows", [])])),
             ("news", lambda: news(ctx, sym, day)), ("upcoming", lambda: upcoming(ctx, sym, day, snap.get("fo") or {}))]
    for name, fn in steps:
        try:
            snap[name] = fn()
        except Exception as e:
            snap[name] = dict(ready=False, why=f"{type(e).__name__}: {str(e)[:160]}")
            ctx.log(f"  {sym} {name}: {type(e).__name__}: {str(e)[:120]}")
    snap["reasons"] = reasons(snap)
    snap["took_s"] = round(time.time() - t0, 1)
    return snap


def save(snap):
    d = DIR / snap["symbol"] / "snaps"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{snap['as_of']}_{time.strftime('%H%M%S')}.json"
    txt = json.dumps(snap, default=_jsonable)
    atomic.write_text(d / name, txt)
    atomic.write_text(DIR / snap["symbol"] / "latest.json", txt)
    return name


def run(symbols, as_of=None, log=print, topup=True):
    syms = [s.strip().upper() for s in symbols if s.strip()]
    if topup:
        try:
            from fetch_angel import login
            from core.provider import AngelProvider
            from core import intraday as I
            log(f"Topping up 5-minute bars for {', '.join(syms)} ...")
            I.run(AngelProvider(login, min_interval=1.0, log=lambda *_: None), syms, "update", log=lambda *_: None)
        except Exception as e:
            log(f"  5-minute top-up skipped: {type(e).__name__}")
    ctx = Ctx(log)
    day = pd.Timestamp(as_of) if as_of else (ctx.panel.date.max() if ctx.panel is not None else ctx.px.date.max())
    day = pd.Timestamp(ctx.days[ctx.days <= np.datetime64(day)][-1])
    done = []
    for s in syms:
        log(f"Analysing {s} as of {day:%d %b %Y} ...")
        try:
            snap = one(ctx, s, day)
            done.append((s, save(snap)))
            log(f"  {s}: saved ({snap['took_s']} s).")
        except Exception as e:
            log(f"  {s}: failed: {type(e).__name__}: {str(e)[:160]}")
    log(f"Stock Desk: {len(done)} of {len(syms)} analysed.")
    return done

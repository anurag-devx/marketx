"""API: Market Lab → Review. Graded calls (live and replayed), calibration, trade plan results and the intraday profile
(threshold crossings) of any graded stock-day. Data: marketlab/review.py."""
import numpy as np, pandas as pd
from web.api import route, UserError

PLAN_MODE = {"oc": "intraday", "gp": "intraday", "cc": "swing"}


def _f(x, d=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), d)


def verdict(misses, market, base, usual, size, label="market"):
    """One line per day: were the misses one sector, a quiet market, or spread out? misses: [{sector, misses, move}]."""
    n = sum(x["misses"] for x in misses)
    arrow = lambda v: "▲" if v > 0.003 else "▼" if v < -0.003 else "•"
    mk = "flat" if abs(market) < 0.003 else ("up" if market > 0 else "down")
    if n == 0:
        return "no misses: every pick moved enough"
    top = max(misses, key=lambda x: x["misses"])
    if n >= 3 and top["misses"] / n >= 0.5:
        return (f"{top['misses']} of {n} misses in {top['sector']}, sector {arrow(top['move'])} {100 * top['move']:+.1f}% → sector move")
    if base is not None and usual and base < 0.75 * usual:
        return (f"quiet day: only {100 * base:.0f}% of all stocks moved ≥{size:g}% (usually {100 * usual:.0f}%), {label} {arrow(market)} {100 * market:+.1f}% → market-wide calm")
    return f"misses spread across sectors on a {mk} {label} ({arrow(market)} {100 * market:+.1f}%) → stock-level"


def _rate(s):
    s = pd.Series(s).dropna()
    return (float(s.mean()), int(len(s))) if len(s) else (None, 0)


@route("GET", "marketlab/review")
def review(p):
    from marketlab import review as RV
    kind = p.get("kind", "all") if p.get("kind") in ("live", "replay", "all") else "all"
    m = p.get("move", "cc") if p.get("move") in ("cc", "oc", "gp") else "cc"
    s = float(p.get("size", 1.0))
    base = dict(kind=kind, move=m, size=s, live_days=RV.days("live"), replay_days=RV.days("replay"))
    calls = RV.load(kind, "calls")
    if calls.empty:
        return dict(base, ready=False, why="Nothing graded yet. Tonight's radar is kept automatically; use Replay to grade past days now.")
    c = calls[(calls.move == m) & (calls["size"] == s)]
    graded = c.dropna(subset=["ret"])
    picks = graded[graded.pick]
    leans = graded[graded.lean != "unclear"]
    hit, n_p = _rate(picks.hit)
    usual = float(picks.usual.mean()) if len(picks) else None
    base_rate, _ = _rate(graded.hit)
    lr, n_l = _rate(leans.lean_right)
    pend = int(c.ret.isna().groupby(c.day).any().sum())
    kpis = [dict(label="Days graded", value=int(graded.day.nunique()), note=f"{pend} waiting for the next session" if pend else "all outcomes in"),
            dict(label=f"Top-{RV.TOP} picks: moved ≥{s:g}%", value=None if hit is None else f"{100 * hit:.1f}%", note=f"{n_p} picks · their usual rate {100 * usual:.1f}%" if usual is not None else ""),
            dict(label="Edge vs usual", value=None if hit is None else f"{100 * (hit - usual):+.1f} pts", note="positive = the picks beat their own usual rate"),
            dict(label="All stocks moved", value=None if base_rate is None else f"{100 * base_rate:.1f}%", note="the day's base rate"),
            dict(label="Lean calls right", value="—" if lr is None else f"{100 * lr:.1f}%", note=f"{n_l} calls (only shown where tested)" if n_l else "no lean shown for this move (not tested)")]
    by_day = []
    for (k, d), g in c.groupby(["kind", "day"]):
        gp = g[g.pick]
        h = gp.hit.dropna()
        by_day.append(dict(day=d, kind=k, picks=int(len(h)), hit=_f(h.mean()) if len(h) else None, usual=_f(gp.usual.mean()),
                           base=_f(g.hit.dropna().mean()) if g.hit.notna().any() else None,
                           leans=int((g.lean != "unclear").sum()), lean_right=_f(g.lean_right.dropna().mean()) if g.lean_right.notna().any() else None,
                           status="graded" if g.ret.notna().all() else "waiting"))
    by_day.sort(key=lambda x: x["day"], reverse=True)
    cal = RV.calibration(graded)
    by_conf = [dict(confidence=k, n=int(len(g)), hit=_f(g.hit.mean()), usual=_f(g.usual.mean())) for k, g in picks.groupby("confidence")]
    prof = RV.load(kind, "profile")
    prof = prof[(prof.move == m) & (prof["size"] == s)] if len(prof) and m != "gp" else pd.DataFrame()
    plans = RV.load(kind, "plans_grade")
    mode = PLAN_MODE[m]
    rows = picks.sort_values(["day", "rank"], ascending=[False, True]).head(400)
    if len(prof):
        rows = rows.merge(prof[["symbol", "day", "kind", "n_cross", "minutes_beyond", "first_cross", "held_to_close", "best_pct", "worst_pct"]], on=["symbol", "day", "kind"], how="left")
    if len(plans):
        pm = plans[plans["mode"] == mode][["symbol", "day", "kind", "status", "exit", "r", "tradeable"]].rename(columns={"status": "plan_status", "exit": "plan_exit", "r": "plan_r"})
        rows = rows.merge(pm, on=["symbol", "day", "kind"], how="left")
    # star map + drivers text for each pick (same as the radar), from the drivers saved with each day
    from web.r_marketlab import _node, node_short
    drv, rad = RV.load(kind, "drivers"), RV.load(kind, "radar")
    nodes, why = {}, {}
    if len(drv):
        drv = drv[(drv.move == m) & (drv["size"] == s)]
        rad_ix = {(x["kind"], x["day"], x["symbol"]): x for x in rad.to_dict("records")} if len(rad) else {}
        for (k, dy, sym), g in drv.groupby(["kind", "day", "symbol"]):
            nodes[(k, dy, sym)] = [dict(node=x.node, **_node(x.d_chance, x.d_up)) for x in g.itertuples()]
            r = rad_ix.get((k, dy, sym))
            if r is not None:
                top = g.assign(w=g.d_chance.abs() + g.d_up.abs()).sort_values("w", ascending=False).head(3)
                why[(k, dy, sym)] = " · ".join(f"{'▲' if x.d_up >= 0.03 else '▼' if x.d_up <= -0.03 else '•'} {x.node} ({node_short(r, x.node)})" for x in top.itertuples() if x.w >= 0.02)
    out_rows = []
    for r in rows.to_dict("records"):
        key = (r["kind"], r["day"], r["symbol"])
        r = dict(r, nodes=nodes.get(key), why=why.get(key))
        out_rows.append({k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items() if k not in ("date",)})
    # misses by sector, per day, with the sector's and the market's move and a one-line verdict
    nifty = None
    try:
        from core import config as C
        ix = pd.read_parquet(C.DATA / "index.parquet", columns=["date", "nifty"]).sort_values("date")
        nifty = ix.assign(nx=ix.nifty.shift(-1) / ix.nifty - 1).set_index("date").nx
    except Exception:
        pass
    sectors = []
    for (k, dy), g in graded.groupby(["kind", "day"]):
        gp = g[g.pick]
        if gp.empty:
            continue
        market = float(g.ret.median())
        ny = float(nifty.get(pd.Timestamp(dy), np.nan)) if nifty is not None and m == "cc" else np.nan
        smove = g.groupby("sector").ret.median()
        tab = []
        for sec, x in gp.groupby("sector"):
            tab.append(dict(sector=sec or "—", picks=int(len(x)), hits=int((x.hit == 1).sum()), misses=int((x.hit == 0).sum()), move=_f(smove.get(sec))))
        tab.sort(key=lambda x: (-x["misses"], -x["picks"]))
        label = "Nifty" if np.isfinite(ny) else "market"
        v = verdict([dict(sector=x["sector"], misses=x["misses"], move=x["move"] or 0.0) for x in tab], ny if np.isfinite(ny) else market,
                    float(g.hit.mean()), float(g.usual.mean()), s, label)
        sectors.append(dict(day=dy, kind=k, verdict=v, market=_f(market), nifty=_f(ny) if np.isfinite(ny) else None, rows=tab))
    sectors.sort(key=lambda x: x["day"], reverse=True)
    psum = None
    if len(plans):
        pp = plans[plans["mode"] == mode]
        done = pp[pp.status == "done"]
        trd = done[done.tradeable == True]  # noqa: E712
        psum = dict(mode=mode, plans=int(len(pp)), entered=int((pp.status.isin(["done", "open"])).sum()), no_entry=int((pp.status == "no entry").sum()),
                    ambiguous=int((pp.status == "ambiguous").sum()), exits={k: int(v) for k, v in done.exit.value_counts().items()},
                    avg_r=_f(done.r.mean(), 2), win=_f((done.r > 0).mean(), 3) if len(done) else None,
                    tradeable_n=int(len(trd)), tradeable_avg_r=_f(trd.r.mean(), 2) if len(trd) else None, tradeable_win=_f((trd.r > 0).mean(), 3) if len(trd) else None)
    second = None
    if "ta_agree" in graded:
        low = graded[graded.confidence == "low"]
        def agree(col):
            out = []
            for k in ("agrees", "disagrees", "neutral", "no data"):
                a, b = graded[graded[col] == k], low[low[col] == k]
                if len(a):
                    out.append(dict(label=k, n=int(a.dir_right.notna().sum()), dir_right=_f(a.dir_right.mean()), n_low=int(b.dir_right.notna().sum()),
                                    dir_right_low=_f(b.dir_right.mean()) if len(b) else None))
            return out
        st_ = graded.assign(setup=graded.ta_setup.fillna("no setup"))
        setup = [dict(setup=k, n=int(g.hit.notna().sum()), hit=_f(g.hit.mean()), usual=_f(g.usual.mean()), edge=_f(g.hit.mean() - g.usual.mean()))
                 for k, g in st_.groupby("setup")]
        second = dict(ta=agree("ta_agree"), fund=agree("fund_agree"), setup=setup)
    return dict(base, ready=True, kpis=kpis, by_day=by_day, calibration=cal.to_dict("records"), by_conf=by_conf, rows=out_rows, plans=psum, second=second, sectors=sectors[:15],
                note=("Replayed days use news and filings as stored now, and today's Scorecard to decide which leans to show: "
                      "they can look slightly better than live calls. Compare live and replay separately." if kind != "live" else None))


@route("GET", "marketlab/profile")
def ml_profile(p):
    from marketlab import review as RV
    from core.data import load_raw
    sym, day = str(p.get("symbol", "")).upper(), str(p.get("day", ""))
    m = p.get("move", "cc") if p.get("move") in ("cc", "oc") else "cc"
    thr = max(0.1, min(10.0, float(p.get("thr", 1.0))))
    band = p.get("band")
    band = None if band in (None, "") else max(0.0, min(thr, float(band)))
    if not sym or not day:
        raise UserError("symbol and day needed")
    px, _, _ = load_raw()
    g = px[px.symbol == sym].sort_values("date")
    nxt = g[g.date > pd.Timestamp(day)].head(1)
    if nxt.empty:
        return dict(ready=False, why="The next session isn't in the data yet.")
    nd = nxt.date.iat[0]
    sess = RV.Bars(since=nd).session(sym, nd)
    if sess.empty:
        return dict(ready=False, why=f"No 5-minute bars for {sym} on {nd:%d %b %Y} (run Download 5-minute bars).")
    prev = g[g.date <= pd.Timestamp(day)].close.tail(1)
    ref = float(prev.iat[0]) if m == "cc" and len(prev) else float(sess.open.iat[0])
    pr = RV.profile(sess, ref, thr, band)
    bars = dict(t=sess.ts.dt.strftime("%Y-%m-%dT%H:%M").tolist(), o=sess.open.round(2).tolist(), h=sess.high.round(2).tolist(), l=sess.low.round(2).tolist(),
                c=sess.close.round(2).tolist(), v=sess.volume.astype(float).tolist(), intraday=True)
    return dict(ready=True, symbol=sym, day=day, session=str(nd.date()), move=m, profile=pr, bars=bars)

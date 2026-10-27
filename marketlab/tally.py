"""Tally: what MarketX projected for one session vs what happened, stock by stock.

Projected (kept in the Review ledger the night before, data/marketlab/ledger/<kind>/<day>/): Market Lab's chance of a move
of at least `size` % and its lean, the TA and fundamentals labels, and tonight's plan (E1/SL/T1/T2).
Happened: the next session's move (graded calls), the plan's result (plans_grade: entered or not, first leg, exit, R) and your
paper takes. Before a day is graded the projections are shown alone; during the session the live feed fills 'so far'."""
import json
import numpy as np, pandas as pd
from core import config as C
from . import review as RV

MODES = ("intraday", "swing")


def days():
    """[(day, kind, graded)], newest first; a day kept both live and replayed shows once, live first."""
    seen, out = set(), []
    for kind in ("live", "replay"):
        for d in RV.days(kind):
            if d not in seen:
                seen.add(d)
                out.append(dict(day=d, kind=kind, graded=_has_outcome(RV._dir(kind, d))))
    return sorted(out, key=lambda x: x["day"], reverse=True)


def _has_outcome(dd):
    f = dd / "calls.parquet"
    try:
        return bool(f.exists() and pd.read_parquet(f, columns=["ret"]).ret.notna().any())
    except Exception:
        return False


def pick_kind(day):
    for kind in ("live", "replay"):
        if (RV._dir(kind, day) / "radar.parquet").exists():
            return kind
    return None


def next_session(day):
    try:
        px = pd.read_parquet(C.DATA / "prices.parquet", columns=["date"]).date.drop_duplicates()
        n = px[px > pd.Timestamp(day)].min()
        if pd.notna(n):
            return str(pd.Timestamp(n).date()), True
    except Exception:
        pass
    n = pd.Timestamp(day) + pd.Timedelta(days=1)
    while n.weekday() >= 5:
        n += pd.Timedelta(days=1)
    return str(n.date()), False


def _plans(kind, day):
    p = RV._dir(kind, day) / "plans.json"
    try:
        return json.loads(p.read_text()).get("plans", {})
    except (OSError, ValueError):
        return {}


def _calls(kind, day, move, size):
    dd = RV._dir(kind, day)
    if (dd / "calls.parquet").exists():
        c = pd.read_parquet(dd / "calls.parquet")
        graded = bool(c.ret.notna().any())
    else:                                                     # not graded yet: rebuild the projections, outcome empty
        from . import evaluate as V
        rd = pd.read_parquet(dd / "radar.parquet")
        outc = rd[["symbol", "date"]].assign(cc=np.nan, oc=np.nan, gp=np.nan)
        c, graded = RV.second_opinion(RV._calls(rd, outc, V.skill_map()[0]), _plans(kind, day)), False
    c = c[(c.move == move) & (c["size"].astype(float) == float(size))]
    return c, graded


def _f(x, d=4):
    try:
        x = float(x)
        return None if np.isnan(x) else round(x, d)
    except (TypeError, ValueError):
        return None


def _agree(label, ret):
    if label not in ("up", "down") or ret is None:
        return None
    return (ret > 0) == (label == "up") if abs(ret) > 1e-9 else None


def build(day, move="cc", size=1.0, mode="intraday", live=None):
    """live: core.live Book when its plan day is this day (session in progress) -> 'so far' outcome from the feed."""
    kind = pick_kind(day)
    if not kind:
        return dict(ready=False, why=f"No projections kept for {day}.")
    calls, graded = _calls(kind, day, move, size)
    plans = _plans(kind, day)
    pg = RV._dir(kind, day) / "plans_grade.parquet"
    pgr = pd.read_parquet(pg) if pg.exists() else pd.DataFrame()
    pgr = pgr[pgr["mode"] == mode].set_index("symbol") if len(pgr) else pgr
    try:
        from core import userdata as U
        takes = [t for t in json.loads(U.path(C.DATA / "paper" / "takes.json", "paper", "takes.json").read_text()) if t.get("day") == day]
    except (OSError, ValueError):
        takes = []
    paper = {t["symbol"]: t for t in takes if t.get("mode", "intraday") == mode}
    for_day, known = next_session(day)
    lv = live.view() if live is not None else None
    lq = {r["symbol"]: r for r in (lv or {}).get("watch", [])}
    ltake = {t["symbol"]: t for t in (lv or {}).get("takes", [])}

    picks = set(calls[calls.pick].symbol)
    rows = []
    cidx = calls.set_index("symbol")
    for sym in sorted(set(picks) | set(plans) | set(paper)):
        c = cidx.loc[sym] if sym in cidx.index else None
        if isinstance(c, pd.DataFrame):
            c = c.iloc[0]
        p = (plans.get(sym) or {}).get(mode) or {}
        g = pgr.loc[sym] if len(pgr) and sym in pgr.index else None
        if isinstance(g, pd.DataFrame):
            g = g.iloc[0]
        ret = _f(c["ret"]) if c is not None else None
        so_far = False
        if ret is None and sym in lq and lq[sym].get("chg") is not None:
            ret, so_far = lq[sym]["chg"] / 100, True
        lean = c["lean"] if c is not None else None
        hit = None if ret is None else abs(ret) >= float(size) / 100
        legs = {f"{x['leg']}{'' if x['side'] == 'both' else ('↑' if x['side'] == 'long' else '↓')}": x["price"] for x in p.get("legs", []) if x.get("price") is not None}
        t = paper.get(sym)
        r = _f(g["r"], 2) if g is not None else None
        lt = ltake.get(sym)
        rows.append(dict(
            symbol=sym, sector=c["sector"] if c is not None else (plans.get(sym) or {}).get("sector"),
            pick=sym in picks, rank=int(c["rank"]) if c is not None else None,
            chance=_f(c["chance"], 3) if c is not None else None, usual=_f(c["usual"], 3) if c is not None else None,
            lean=lean, confidence=c["confidence"] if c is not None else None,
            ta=(plans.get(sym) or {}).get("ta", {}).get("dir"), fund=(plans.get(sym) or {}).get("fund", {}).get("dir"),
            ret=ret, so_far=so_far, hit=hit, lean_right=_agree(lean, ret),
            ta_right=_agree((plans.get(sym) or {}).get("ta", {}).get("dir"), ret), fund_right=_agree((plans.get(sym) or {}).get("fund", {}).get("dir"), ret),
            plan_lean=p.get("lean"), tradeable=p.get("tradeable"), legs=legs,
            status=(g["status"] if g is not None else ("live" if lt and lt.get("state") not in (None, "waiting") else None)),
            side=(g["side"] if g is not None else (lt or {}).get("side")), entry=_f(g["entry"], 2) if g is not None else (lt or {}).get("entry"),
            first=g["first"] if g is not None else None, exit=g["exit"] if g is not None else None,
            exit_price=_f(g["exit_price"], 2) if g is not None else None, r=r if g is not None else (lt or {}).get("r"),
            fired=(lq.get(sym) or {}).get("fired", []),
            paper=bool(t), risk_rs=(t or {}).get("risk_rs"),
            pnl_rs=None if not t or r is None else round(r * float(t.get("risk_rs") or 0)),
        ))
    return dict(ready=True, day=day, kind=kind, for_day=for_day, for_known=known, graded=graded, live=lv is not None,
                move=move, size=size, mode=mode, rows=rows, kpis=kpis(rows))


def kpis(rows):
    pk = [r for r in rows if r["pick"]]
    pko = [r for r in pk if r["hit"] is not None]
    ln = [r for r in pk if r["lean_right"] is not None]
    pl = [r for r in rows if r["r"] is not None and r["entry"] is not None]
    plt = [r for r in pl if r["tradeable"]]
    pp = [r for r in rows if r["paper"]]
    ppd = [r for r in pp if r["pnl_rs"] is not None]
    side = lambda key: (sum(1 for r in rows if r[key]), sum(1 for r in rows if r[key] is not None))
    mean = lambda xs: None if not xs else round(float(np.mean(xs)), 3)
    return dict(
        picks=len(pk), picks_done=len(pko), picks_hit=sum(r["hit"] for r in pko),
        said=mean([r["chance"] for r in pko if r["chance"] is not None]), happened=mean([float(r["hit"]) for r in pko]),
        usual=mean([r["usual"] for r in pko if r["usual"] is not None]),
        lean_n=len(ln), lean_right=sum(r["lean_right"] for r in ln),
        ta=side("ta_right"), fund=side("fund_right"),
        plans_entered=len(pl), plans_r=round(sum(r["r"] for r in pl), 2) if pl else None, plans_avg_r=mean([r["r"] for r in pl]),
        plans_win=sum(1 for r in pl if r["r"] > 0), tradeable_n=len(plt), tradeable_r=round(sum(r["r"] for r in plt), 2) if plt else None,
        stop_first=sum(1 for r in pl if r["first"] == "SL"), target_first=sum(1 for r in pl if r["first"] in ("T1", "T2")),
        paper=len(pp), paper_done=len(ppd), paper_rs=sum(r["pnl_rs"] for r in ppd) if ppd else None)


def bars(symbol, for_day):
    """The session's 5-min bars for the chart (data/intraday/5min)."""
    from core import intraday as ID
    f = ID.BARS / f"{symbol}.parquet"
    if not f.exists():
        return None
    d = pd.read_parquet(f)
    d = d[d.ts.dt.date == pd.Timestamp(for_day).date()].sort_values("ts")
    if d.empty:
        return None
    return dict(t=[x.strftime("%Y-%m-%dT%H:%M:00") for x in d.ts], o=d.open.round(2).tolist(), h=d.high.round(2).tolist(),
                l=d.low.round(2).tolist(), c=d.close.round(2).tolist(), v=d.volume.astype(float).tolist())

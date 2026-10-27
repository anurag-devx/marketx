"""API: Tally (projected vs what happened, per session). Data: the Review ledger + paper takes; the live feed during the session."""
import re
from web.api import route, UserError

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _live_for(day):
    try:
        from core import live
        f = live.FEED
        return f.book if f.book is not None and getattr(f, "plan_day", None) == day and f.mode == "live" else None
    except Exception:
        return None


@route("GET", "tally/days")
def tdays(_):
    from marketlab import tally as T
    return dict(items=T.days())


@route("GET", "tally")
def tally(p):
    from marketlab import tally as T
    ds = T.days()
    day = p.get("day") or (next((x["day"] for x in ds if x["graded"]), None) or (ds[0]["day"] if ds else None))
    if not day:
        return dict(ready=False, why="Nothing kept yet: build the radar tonight (or Market Lab → Review → Replay).", days=[])
    if not DAY.match(str(day)):
        raise UserError("day must be YYYY-MM-DD")
    move = p.get("move") if p.get("move") in ("cc", "oc", "gp") else "cc"
    mode = p.get("mode") if p.get("mode") in T.MODES else "intraday"
    try:
        size = float(p.get("size") or 1.0)
    except ValueError:
        size = 1.0
    out = T.build(day, move, size, mode, live=_live_for(day))
    out["days"] = ds
    return out


@route("GET", "tally/chart")
def chart(p):
    from marketlab import tally as T
    sym, day = str(p.get("symbol") or "").upper(), str(p.get("day") or "")
    if not SYM.match(sym) or not DAY.match(day):
        raise UserError("pick a stock and a day")
    bk = _live_for(day)
    if bk is not None and sym in bk.watch:
        c = bk.chart(sym)
        return dict(bars=c["bars"], for_day=c["bars"]["t"][0][:10] if c["bars"]["t"] else None, live=True)
    for_day, _ = T.next_session(day)
    return dict(bars=T.bars(sym, for_day), for_day=for_day, live=False)


@route("POST", "marketlab/assistant")
def assistant(_, body):
    """Grades assistant: question -> fixed query (model or keywords) -> computed table + 2-4 lines (numbers checked)."""
    from marketlab import assistant as A
    b = body or {}
    day = b.get("day") if DAY.match(str(b.get("day") or "")) else None
    move = b.get("move") if b.get("move") in ("cc", "oc", "gp") else "cc"
    mode = b.get("mode") if b.get("mode") in ("intraday", "swing") else "intraday"
    try:
        size = float(b.get("size") or 1.0)
    except ValueError:
        size = 1.0
    return A.answer(str(b.get("question") or ""), day=day, move=move, size=size, mode=mode, word=bool(b.get("word")))


def _clean(r):
    """DataFrame rows back to plain values: NaN -> None (the Tally counts treat None as 'not graded')."""
    import math
    return {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}


def _opts(p):
    from marketlab import tally as T
    move = p.get("move") if p.get("move") in ("cc", "oc", "gp") else "cc"
    mode = p.get("mode") if p.get("mode") in T.MODES else "intraday"
    try:
        size = float(p.get("size") or 1.0)
    except ValueError:
        size = 1.0
    return move, size, mode


@route("GET", "tally/compare")
def compare(p):
    """Top numbers for several graded days side by side (oldest first), from the same rows as the Tally page."""
    from marketlab import tally as T, assistant as A
    move, size, mode = _opts(p)
    graded = [x["day"] for x in T.days() if x["graded"]]
    want = [d for d in str(p.get("days") or "").split(",") if DAY.match(d)]
    days = sorted([d for d in want if d in graded] or graded[:10])[:40]
    f = A.rows(days, move, size, mode)
    out = []
    for d in days:
        rows = [_clean(r) for r in f[f.day == d].to_dict("records")] if len(f) else []
        k = T.kpis(rows)
        out.append(dict(day=d, for_day=rows[0]["for_day"] if rows else None, **{x: k[x] for x in (
            "picks", "picks_done", "picks_hit", "said", "happened", "usual", "lean_n", "lean_right", "plans_entered", "plans_r", "plans_avg_r",
            "plans_win", "target_first", "stop_first", "paper", "paper_rs")}, ta_right=k["ta"][0], ta_n=k["ta"][1]))
    return dict(days=out, available=graded, move=move, size=size, mode=mode)


@route("GET", "tally/stock")
def stock_history(p):
    """One stock across every graded day: what MarketX said and what happened."""
    from marketlab import tally as T, assistant as A
    sym = str(p.get("symbol") or "").upper().strip()
    if not SYM.match(sym):
        raise UserError("pick a stock")
    move, size, mode = _opts(p)
    graded = [x["day"] for x in T.days() if x["graded"]][:60]
    f = A.rows(graded, move, size, mode)
    f = f[f.symbol == sym] if len(f) else f
    keep = ("day", "for_day", "pick", "rank", "chance", "usual", "lean", "confidence", "ta", "fund", "ret", "hit", "lean_right", "ta_right",
            "plan_lean", "status", "side", "entry", "exit", "exit_price", "r", "paper", "pnl_rs", "legs")
    rows = [{k: r.get(k) for k in keep} for r in map(_clean, f.sort_values("day", ascending=False).to_dict("records"))] if len(f) else []
    g = [r for r in rows if r["hit"] is not None]
    summary = dict(days=len(rows), graded=len(g), picks=sum(1 for r in rows if r["pick"]), hit=sum(1 for r in g if r["hit"]),
                   pick_hit=sum(1 for r in g if r["pick"] and r["hit"]), pick_graded=sum(1 for r in g if r["pick"]),
                   plans_entered=sum(1 for r in rows if r["r"] is not None), plans_r=round(sum(r["r"] for r in rows if r["r"] is not None), 2))
    return dict(symbol=sym, rows=rows, summary=summary, move=move, size=size, mode=mode)

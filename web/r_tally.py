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
    return A.answer(str(b.get("question") or ""), day=day, move=move, size=size, mode=mode)

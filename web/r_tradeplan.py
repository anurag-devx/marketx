"""API: Trade plan (secondary to Market Lab): the saved plan for one stock, with recent bars, levels and candle patterns."""
import pandas as pd
from web.api import route, UserError, prices
from core import jobs


@route("GET", "tradeplan")
def tradeplan(p):
    from tradeplan import plan as TP, patterns as PT
    sym = str(p.get("symbol", "")).upper().strip()
    mode = p.get("mode", "intraday")
    if mode not in TP.MODES:
        raise UserError("mode must be intraday or swing")
    saved = TP.load()
    ta = PT.talib_status()
    base = dict(symbol=sym, mode=mode, talib=ta, version=TP.VERSION)
    if not saved or sym not in saved.get("plans", {}):
        return dict(base, ready=False, why="No plan for this stock yet: it's built with tonight's Market Lab radar (Market Lab → Scorecard → Build tonight's radar).")
    e = saved["plans"][sym]
    px = prices()
    g = px[px.symbol == sym].sort_values("date").tail(90) if px is not None else pd.DataFrame()
    bars = dict(t=g.date.dt.strftime("%Y-%m-%d").tolist(), o=g.open.round(2).tolist(), h=g.high.round(2).tolist(), l=g.low.round(2).tolist(),
                c=g.close.round(2).tolist(), v=g.volume.astype(float).tolist()) if len(g) else None
    return dict(base, ready=True, day=saved["day"], plan=e[mode], patterns=e.get("patterns", []), patterns_source=saved.get("patterns_source"), bars=bars)


@route("GET", "tradeplan/summary")
def summary(p):
    """One row per stock for the Move Radar: tradeable?, reward:risk, entries/exits, first flag."""
    from tradeplan import plan as TP
    mode = p.get("mode", "intraday")
    saved = TP.load() or {}
    out = {}
    for sym, e in (saved.get("plans") or {}).items():
        x = e.get(mode) or {}
        out[sym] = dict(ok=bool(x.get("tradeable")), rr=max((s_.get("rr") or 0) for s_ in x.get("sides", [{}])) if x.get("sides") else None,
                        n=f"{x.get('n_entries', 0)} / {x.get('n_exits', 0)}", why=(x.get("flags") or [""])[0])
    return dict(day=saved.get("day"), rows=out)

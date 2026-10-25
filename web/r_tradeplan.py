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
    from tradeplan import agent as AG
    summary = None
    try:
        summary = AG.template(_facts(sym, mode, saved, px))
    except Exception:
        pass
    return dict(base, ready=True, day=saved["day"], plan=e[mode], patterns=e.get("patterns", []), patterns_source=saved.get("patterns_source"), bars=bars, summary=summary)


def _facts(sym, mode, saved, px=None):
    """Computed facts for the TA agent: the saved plan, recent bars up to the plan's day, and tonight's Market Lab verdict."""
    from tradeplan import agent as AG, plan as TP
    from marketlab import radar as R
    e = saved["plans"][sym]
    px = prices() if px is None else px
    g = px[(px.symbol == sym) & (px.date <= pd.Timestamp(saved["day"]))].sort_values("date").tail(60)
    verdict = None
    rd = R.read("radar", saved["day"])
    m = TP.MODES[mode]["move"]
    r = rd[rd.symbol == sym]
    if len(r):
        r = r.iloc[0]
        verdict = dict(chance_pct=round(100 * float(r[f"ch_{m}_1.0"])), usual_pct=round(100 * float(r[f"us_{m}_1.0"])), lean=e[mode]["lean"])
    f = AG.facts(sym, mode, e[mode], e.get("patterns", []), g, verdict)
    if e.get("ta"):
        f["second_opinion_chart"] = {k: e["ta"].get(k) for k in ("dir", "setup", "why")}
    if e.get("fund"):
        f["second_opinion_fundamentals"] = {k: e["fund"].get(k) for k in ("dir", "why")}
    f["second_opinion_note"] = "labels only, not tested on history yet"
    return f


@route("POST", "tradeplan/explain")
def explain(_, body):
    """TA agent on demand (background task): the local model words the plan; the template is used if it's off or adds numbers."""
    from tradeplan import plan as TP, agent as AG
    from web import tasks
    sym, mode = str((body or {}).get("symbol", "")).upper().strip(), (body or {}).get("mode", "intraday")
    saved = TP.load()
    if mode not in TP.MODES or not saved or sym not in saved.get("plans", {}):
        raise UserError("no plan for that stock yet")

    def job(log, progress):
        progress(0.2, "Collecting the computed facts ...")
        f = _facts(sym, mode, saved)
        progress(0.4, "Asking the TA agent ...")
        text, writer = AG.explain(f, log)
        return dict(text=text, writer=writer)
    try:
        return dict(task=tasks.start("ta-agent", job))
    except RuntimeError as ex:
        raise UserError(str(ex))


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
                        n=f"{x.get('n_entries', 0)} / {x.get('n_exits', 0)}", why=(x.get("flags") or [""])[0],
                        ta=(e.get("ta") or {}).get("dir"), fund=(e.get("fund") or {}).get("dir"),
                        second_why="TA: " + "; ".join((e.get("ta") or {}).get("why", [])) + ((" · setup: " + e["ta"]["setup"]) if (e.get("ta") or {}).get("setup") else "")
                                   + " | Fundamentals: " + "; ".join((e.get("fund") or {}).get("why", [])) + ". Labels only: graded in Review.")
    return dict(day=saved.get("day"), rows=out)


@route("POST", "tradeplan/ask")
def ask(_, body):
    """Follow-up question to the TA agent about one stock's plan (background task). Answers only from the computed facts."""
    from tradeplan import plan as TP, agent as AG
    from web import tasks
    b = body or {}
    sym, mode, q = str(b.get("symbol", "")).upper().strip(), b.get("mode", "intraday"), str(b.get("question", "")).strip()
    if not q:
        raise UserError("type a question")
    if len(q) > 500:
        raise UserError("keep the question under 500 characters")
    hist = [dict(q=str(h.get("q", ""))[:500], a=str(h.get("a", ""))[:1500]) for h in (b.get("history") or [])[-6:] if isinstance(h, dict)]
    saved = TP.load()
    if mode not in TP.MODES or not saved or sym not in saved.get("plans", {}):
        raise UserError("no plan for that stock yet")

    def job(log, progress):
        progress(0.3, "Collecting the computed facts ...")
        f = _facts(sym, mode, saved)
        progress(0.5, "Asking the TA agent ...")
        text, writer = AG.ask(f, q, hist, log)
        return dict(text=text, writer=writer)
    try:
        return dict(task=tasks.start("ta-agent", job))
    except RuntimeError as ex:
        raise UserError(str(ex))

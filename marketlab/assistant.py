"""Grades assistant (Tally / Review): answers questions about what MarketX projected vs what happened.

Rule as everywhere in MarketX: numbers never come from the AI.
  1. route   the question -> one of the fixed questions below + its settings (days, stock, sector, grouping ...).
             The local model picks them (JSON); without a model, keywords do.
  2. compute Python works the answer out from the Review ledger (marketlab/tally.py rows): a summary and a table.
  3. write   the model writes 2-4 lines from that summary; any number not in the computed facts -> the plain summary instead.
"""
import json, re
import numpy as np, pandas as pd
from . import tally as T

INTENTS = {
    "matched": "stocks whose projection came true (moved at least the size, in the lean's direction when a lean was shown)",
    "missed": "stocks whose projection did not come true",
    "hit_rate": "said vs happened: projected chance vs how often the move came, optionally grouped by day / sector / confidence / move",
    "plans": "trade plan results: R, target-first vs stop-first, best or worst plans",
    "paper": "the user's paper takes and their result",
    "second": "how often the TA and fundamentals labels pointed the right way",
    "stock": "one stock's history: every day it was projected and what happened",
    "trend": "is accuracy getting better or worse over the days",
    "overview": "a summary of one day (or the range)",
}
BY = ("day", "sector", "confidence", "move")
MAX_DAYS = 60


# ---------------------------------------------------------------- data: tally rows for one or more days (cached)
_cache = {}


def _stamp(day):
    kind = T.pick_kind(day)
    d = T.RV._dir(kind, day) if kind else None
    return tuple((f.name, f.stat().st_mtime) for f in sorted(d.glob("*.parquet"))) if d else ()


def rows(days, move="cc", size=1.0, mode="intraday"):
    out = []
    for day in days:
        key = (day, move, float(size), mode)
        st = _stamp(day)
        if key not in _cache or _cache[key][0] != st:
            b = T.build(day, move, size, mode)
            _cache[key] = (st, [dict(r, day=day, for_day=b.get("for_day")) for r in b.get("rows", [])] if b.get("ready") else [])
        out += _cache[key][1]
    return pd.DataFrame(out)


def graded_days():
    return [x["day"] for x in T.days() if x["graded"]]


def plan_days(mode="intraday"):
    out = []
    for d in graded_days():
        k = T.pick_kind(d)
        f = T.RV._dir(k, d) / "plans_grade.parquet" if k else None
        try:
            x = pd.read_parquet(f, columns=["mode", "entry"]) if f and f.exists() else None
            if x is not None and x[x["mode"] == mode].entry.notna().any():
                out.append(d)
        except Exception:
            pass
    return out


def pick_days(spec, default_day=None, plans=False, mode="intraday"):
    """spec: 'latest' | 'all' | int N (last N graded days) | 'YYYY-MM-DD'. plans: only days whose plans are graded."""
    g = plan_days(mode) if plans else graded_days()
    if not g:
        return []
    if spec in (None, "", "latest", "current"):
        return [default_day] if default_day in g else g[:1]
    if spec == "all":
        return g[:MAX_DAYS]
    if isinstance(spec, (int, float)) or str(spec).isdigit():
        return g[:max(1, min(MAX_DAYS, int(spec)))]
    s = str(spec)[:10]                                  # a date means the session that happened that day
    for d in g:
        if T.next_session(d)[0] == s:
            return [d]
    return [s] if s in g else [d for d in g if d < s][:1]


# ---------------------------------------------------------------- the fixed questions
def _matched(f):
    return (f.hit == True) & (f.lean_right != False)        # noqa: E712  (None = no lean shown: the move alone counts)


def _graded(f):
    return f[f.hit.notna()] if len(f) and "hit" in f else f.iloc[0:0]


def _pct(x, d=1):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(100 * float(x), d)


def _scope(f, p):
    if not len(f):
        return f
    if p.get("symbol"):
        f = f[f.symbol == p["symbol"]]
    if p.get("sector"):
        f = f[f.sector.fillna("").str.lower().str.startswith(p["sector"].lower()[:10])]
    if p.get("scope", "picks") == "picks" and not p.get("symbol"):
        f = f[f.pick]
    return f


STOCK_COLS = [("day", "Projected on", "day"), ("symbol", "Stock", "sym"), ("chance", "Said: chance", "pct"), ("lean", "Said: lean", "dir"),
              ("ret", "Moved", "ret"), ("hit", "Size reached", "ok"), ("lean_right", "Lean right", "ok")]


def _table(f, cols, limit=50):
    f = f.head(limit)
    keep = [c for c, _, _ in cols]
    recs = [{k: (None if (isinstance(v, float) and np.isnan(v)) else v) for k, v in r.items() if k in keep} for r in f.to_dict("records")]
    return dict(columns=[dict(key=c, label=l, type=t) for c, l, t in cols], rows=recs)


def q_matched(f, p, miss=False):
    g = _graded(_scope(f, p))
    m = g[~_matched(g)] if miss else g[_matched(g)]
    m = m.assign(abs_ret=m.ret.abs()).sort_values("abs_ret", ascending=miss)
    facts = dict(graded=len(g), count=len(m), share_pct=_pct(len(m) / len(g)) if len(g) else None,
                 stocks=[dict(symbol=r.symbol, day=r.day, moved_pct=_pct(r.ret, 2), said_pct=_pct(r.chance, 0), lean=r.lean) for r in m.head(10).itertuples()])
    title = f"{'Not matched' if miss else 'Matched'}: {len(m)} of {len(g)}"
    return title, facts, _table(m, STOCK_COLS)


def q_hit_rate(f, p):
    g = _graded(_scope(f, p))
    by = p.get("by") if p.get("by") in BY else None
    if by == "move":
        by = None
    if not len(g):
        return "Hit rate", dict(graded=0), _table(g, STOCK_COLS)
    if by:
        t = g.groupby(by).agg(n=("symbol", "size"), said=("chance", "mean"), happened=("hit", "mean"), usual=("usual", "mean")).reset_index()
        t = t.sort_values(by, ascending=by != "day") if by == "day" else t.sort_values("n", ascending=False)
        t["said"], t["happened"], t["usual"] = (t[c].astype(float).map(lambda x: _pct(x)) for c in ("said", "happened", "usual"))
        facts = dict(graded=len(g), by=by, groups=t.head(15).to_dict("records"))
        return f"Said vs happened by {by}", facts, dict(columns=[dict(key=by, label=by.title(), type="text"), dict(key="n", label="Stocks", type="int"),
                                                                     dict(key="said", label="Said %", type="num"), dict(key="happened", label="Happened %", type="num"),
                                                                     dict(key="usual", label="Usual %", type="num")], rows=t.to_dict("records"))
    facts = dict(graded=len(g), said_pct=_pct(g.chance.mean()), happened_pct=_pct(g.hit.astype(float).mean()), usual_pct=_pct(g.usual.mean()),
                 matched=int(_matched(g).sum()))
    return "Said vs happened", facts, _table(g.sort_values("chance", ascending=False), STOCK_COLS)


def q_plans(f, p):
    g = f[f.r.notna() & f.entry.notna()] if len(f) else f
    if p.get("symbol"):
        g = g[g.symbol == p["symbol"]]
    worst = p.get("order") == "worst"
    g = g.sort_values("r", ascending=worst)
    cols = [("day", "Projected on", "day"), ("symbol", "Stock", "sym"), ("side", "Side", "text"), ("entry", "Entry", "num"), ("first", "First", "text"),
            ("exit", "Exit", "text"), ("r", "R", "r"), ("tradeable", "Passed checks", "ok")]
    facts = dict(entered=len(g), total_r=round(float(g.r.sum()), 2) if len(g) else None, avg_r=round(float(g.r.mean()), 2) if len(g) else None,
                 winners=int((g.r > 0).sum()), losers=int((g.r < 0).sum()), target_first=int(g["first"].isin(["T1", "T2"]).sum()),
                 stop_first=int((g["first"] == "SL").sum()), order="worst" if worst else "best",
                 top=[dict(symbol=r.symbol, day=r.day, r=round(float(r.r), 2), exit=r.exit) for r in g.head(8).itertuples()])
    return f"Plans: {'worst' if worst else 'best'} first", facts, _table(g, cols)


def q_paper(f, p):
    g = f[f.paper] if len(f) else f
    cols = [("day", "Projected on", "day"), ("symbol", "Stock", "sym"), ("side", "Side", "text"), ("entry", "Entry", "num"), ("exit", "Exit", "text"),
            ("r", "R", "r"), ("pnl_rs", "P&L ₹", "int")]
    done = g[g.pnl_rs.notna()] if len(g) else g
    facts = dict(taken=len(g), graded=len(done), pnl_rs=int(done.pnl_rs.sum()) if len(done) else None, avg_r=round(float(done.r.mean()), 2) if len(done) else None,
                 wins=int((done.r > 0).sum()) if len(done) else 0)
    return "Your paper takes", facts, _table(g, cols)


def q_second(f, p):
    g = _graded(_scope(f, dict(p, scope="all") if p.get("scope") == "all" else p))
    out, rows_ = {}, []
    for k, lab in (("ta_right", "TA"), ("fund_right", "Fundamentals"), ("lean_right", "Market Lab lean")):
        s = g[k].dropna() if k in g else pd.Series(dtype=float)
        out[lab] = dict(labelled=len(s), right=int(s.astype(bool).sum()), right_pct=_pct(s.astype(float).mean()) if len(s) else None)
        rows_.append(dict(label=lab, **out[lab]))
    return "Direction: who was right", dict(graded=len(g), **out), dict(columns=[dict(key="label", label="Label", type="text"), dict(key="labelled", label="Called up/down", type="int"),
                                                                                 dict(key="right", label="Right", type="int"), dict(key="right_pct", label="Right %", type="num")], rows=rows_)


def q_stock(f, p):
    g = f[f.symbol == p.get("symbol")] if len(f) else f
    cols = STOCK_COLS + [("r", "Plan R", "r"), ("ta", "TA", "dir")]
    gg = _graded(g)
    facts = dict(symbol=p.get("symbol"), sessions=len(g), picks=int(g.pick.sum()) if len(g) else 0, matched=int(_matched(gg).sum()) if len(gg) else 0,
                 graded=len(gg), history=[dict(day=r.day, pick=bool(r.pick), moved_pct=_pct(r.ret, 2), said_pct=_pct(r.chance, 0), r=r.r) for r in g.head(10).itertuples()])
    return f"{p.get('symbol')}: history", facts, _table(g.sort_values("day", ascending=False), cols)


def q_trend(f, p):
    g = _graded(_scope(f, p))
    if not len(g):
        return "Trend", dict(graded=0), _table(g, STOCK_COLS)
    t = g.groupby("day").agg(n=("symbol", "size"), happened=("hit", "mean"), said=("chance", "mean"), matched=("symbol", lambda s: int(_matched(g.loc[s.index]).sum()))).reset_index().sort_values("day")
    t["happened"], t["said"] = t.happened.astype(float).map(_pct), t.said.astype(float).map(_pct)
    h = len(t) // 2
    early, late = (t.iloc[:h], t.iloc[h:]) if h else (t.iloc[0:0], t)
    w = lambda x: None if not len(x) else round(float((x.happened * x.n).sum() / x.n.sum()), 1)
    facts = dict(sessions=len(t), first_half_pct=w(early), second_half_pct=w(late), by_day=t.tail(15).to_dict("records"))
    return "Trend over the days", facts, dict(columns=[dict(key="day", label="Projected on", type="day"), dict(key="n", label="Stocks", type="int"),
                                                      dict(key="said", label="Said %", type="num"), dict(key="happened", label="Happened %", type="num"),
                                                      dict(key="matched", label="Matched", type="int")], rows=t.sort_values("day", ascending=False).to_dict("records"))


def q_overview(f, p):
    title, facts, table = q_hit_rate(f, p)
    pl = q_plans(f, p)[1]
    facts.update(plans_entered=pl["entered"], plans_total_r=pl["total_r"], plans_target_first=pl["target_first"], plans_stop_first=pl["stop_first"])
    return "Overview", facts, table


RUN = dict(matched=q_matched, missed=lambda f, p: q_matched(f, p, miss=True), hit_rate=q_hit_rate, plans=q_plans, paper=q_paper,
           second=q_second, stock=q_stock, trend=q_trend, overview=q_overview)


# ---------------------------------------------------------------- routing
SECTOR_WORDS = {"bank": "Financial Services", "banking": "Financial Services", "nbfc": "Financial Services", "financial": "Financial Services",
                "it": "Information Technology", "tech": "Information Technology", "software": "Information Technology",
                "pharma": "Healthcare", "healthcare": "Healthcare", "hospital": "Healthcare", "auto": "Automobile", "automobile": "Automobile",
                "fmcg": "Fast Moving Consumer Goods", "metal": "Metals", "metals": "Metals", "mining": "Metals", "realty": "Realty",
                "real estate": "Realty", "oil": "Oil Gas", "gas": "Oil Gas", "energy": "Oil Gas", "power": "Power", "media": "Media",
                "chemical": "Chemicals", "chemicals": "Chemicals", "capital goods": "Capital Goods", "construction": "Construction",
                "textile": "Textiles", "telecom": "Telecommunication", "durables": "Consumer Durables", "cement": "Construction Materials"}


def keywords(q, symbols=()):
    s, up = q.lower(), q.upper()
    p = {}
    for w, sec in SECTOR_WORDS.items():
        if re.search(rf"\b{re.escape(w)}\b", s) and not (w == "it" and not re.search(r"\b(it sector|it stocks|in it|it companies)\b", s)):
            p["sector"] = sec
            break
    m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", q)
    if m:
        p["days"] = m.group(1)
    m = re.search(r"last\s+(\d+)\s*(day|session|week)", s)
    if m:
        p["days"] = int(m.group(1)) * (5 if m.group(2).startswith("week") else 1)
    elif re.search(r"\b(all|overall|ever|so far|every day|all days)\b", s):
        p["days"] = "all"
    elif re.search(r"\b(this|last|past)\s+week\b", s):
        p["days"] = 5
    syms = [x for x in symbols if re.search(rf"(?<![A-Z0-9&]){re.escape(x)}(?![A-Z0-9&])", up)]
    if syms:
        p["symbol"] = max(syms, key=len)
    for b in BY:
        if re.search(rf"\b(by|per|each)\s+{b}", s):
            p["by"] = b
    if re.search(r"\b(worst|lost|loss|losing|bad)\b", s):
        p["order"] = "worst"
    if re.search(r"\ball stocks\b|\bevery stock\b", s):
        p["scope"] = "all"
    if re.search(r"open[- ]to[- ]close|intraday move", s):
        p["move"] = "oc"
    elif re.search(r"\bgap\b", s):
        p["move"] = "gp"
    m = re.search(r"(\d(?:\.5)?)\s*%", s)
    if m and float(m.group(1)) in (0.5, 1, 2, 3):
        p["size"] = float(m.group(1))
    if re.search(r"not match|didn.?t match|unmatched|miss|wrong|fail|did not", s):
        i = "missed"
    elif re.search(r"\bpaper\b", s):
        i = "paper"
    elif re.search(r"\bplans?\b|\btarget|\bstop\b|\b r\b|\bentr", s):
        i = "plans"
    elif re.search(r"\bta\b|technical|fundamental|second opinion", s):
        i = "second"
    elif re.search(r"trend|improv|better|worse|getting", s):
        i = "trend"
    elif re.search(r"hit rate|accura|calibrat|said vs|how often|chance", s) or ("by" in p and not re.search(r"match", s)):
        i = "hit_rate"
    elif re.search(r"match|came true|correct|right|hit\b|worked", s):
        i = "matched"
    elif "symbol" in p:
        i = "stock"
    else:
        i = "overview"
    p["intent"] = i
    return p


ROUTER = ("You route questions about a stock-prediction scorecard to ONE fixed query. Reply with JSON only: "
          '{"intent": one of ' + json.dumps(list(INTENTS)) + ', "days": "latest" | "all" | <number of recent days> | "YYYY-MM-DD", '
          '"symbol": NSE symbol or null, "sector": text or null, "by": one of ' + json.dumps(list(BY)) + ' or null, "order": "best"|"worst"|null, '
          '"scope": "picks"|"all"}. Intents: ' + "; ".join(f"{k} = {v}" for k, v in INTENTS.items()) + ". "
          'Defaults: days "latest", scope "picks". "yesterday"/"today"/"last session" = "latest". Never answer the question itself.')


def route(q, symbols=(), use_model=True):
    kw = keywords(q, symbols)
    if not use_model or kw["intent"] != "overview":        # keywords understood it: no need to wait for the model
        return kw, "keywords"
    try:
        from core import llm
        ok, _ = llm.status("ask")
        if not ok:
            return kw, "keywords"
        raw = llm.chat("ask", [{"role": "user", "content": q[:500]}], system=ROUTER, max_tokens=200, json_mode=True, timeout=60)
        j = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:
        return kw, "keywords"
    p = {}
    p["intent"] = j.get("intent") if j.get("intent") in INTENTS else kw["intent"]
    d = j.get("days")
    p["days"] = kw.get("days", d if d in ("latest", "all") or (isinstance(d, int) and d > 0) or (isinstance(d, str) and re.fullmatch(r"\d{4}-\d\d-\d\d|\d+", d)) else "latest")
    sym = str(j.get("symbol") or "").upper()
    p["symbol"] = kw.get("symbol") or (sym if sym in set(symbols) else None)
    if kw.get("sector") or j.get("sector"):
        p["sector"] = kw.get("sector") or str(j["sector"])[:40]
    p["by"] = kw.get("by") or (j.get("by") if j.get("by") in BY else None)
    p["order"] = kw.get("order") or (j.get("order") if j.get("order") in ("best", "worst") else None)
    p["scope"] = kw.get("scope") or (j.get("scope") if j.get("scope") in ("picks", "all") else "picks")
    for k in ("move", "size"):
        if k in kw:
            p[k] = kw[k]
    if p["symbol"] and p["intent"] == "overview":
        p["intent"] = "stock"
    return {k: v for k, v in p.items() if v is not None}, "model"


# ---------------------------------------------------------------- answer
def template(title, facts):
    def fmt(v):
        return ", ".join(f"{k.replace('_', ' ')} {fmt(x)}" for k, x in v.items()) if isinstance(v, dict) else str(v)
    parts = [f"{k.replace('_', ' ')}: {fmt(v)}" for k, v in facts.items() if not isinstance(v, list) and v is not None]
    return f"{title}. " + "; ".join(parts) + "."


WRITER = ("You explain a computed scorecard result to a trader in 2-4 short plain sentences. Use ONLY the numbers in FACTS, "
          "written exactly as given (percent values are already in %). Name the stocks when FACTS lists them. No advice, no predictions, no hedging filler.")


def write(question, title, facts, use_model=True):
    if not use_model:
        return template(title, facts), "template"
    try:
        from core import llm
        from tradeplan.agent import check
        ok, _ = llm.status("ask")
        if not ok:
            return template(title, facts), "template"
        out = llm.chat("ask", [{"role": "user", "content": f"QUESTION: {question[:500]}\nRESULT: {title}\nFACTS: {json.dumps(facts, default=str)}"}],
                       system=WRITER, max_tokens=300, timeout=120).strip()
        if not out or check(out, facts, question + " " + title):
            return template(title, facts), "template"
        return out, "model"
    except Exception:
        return template(title, facts), "template"


def answer(question, day=None, move="cc", size=1.0, mode="intraday", use_model=True, word=True):
    """word=False: table + plain summary at once (the page then asks again with word=True for the AI's wording)."""
    q = str(question or "").strip()[:500]
    if not q:
        return dict(answer="Ask about matches, misses, hit rate, plans, paper takes, TA / fundamentals, a stock, or the trend.", rows=[], columns=[])
    g = graded_days()
    if not g:
        return dict(answer="Nothing is graded yet: a day is graded after the next session's prices are downloaded.", rows=[], columns=[])
    probe = rows(g[:1], move, size, mode)
    symbols = sorted(set(probe.symbol)) if len(probe) else []
    p, router = route(q, symbols, use_model)
    move, size = p.get("move", move), float(p.get("size", size))
    spec = p.get("days", "latest")
    if p.get("intent") in ("trend",) and spec in (None, "latest"):
        spec = "all"
    if p.get("intent") == "stock" and spec in (None, "latest"):
        spec = "all"
    days = pick_days(spec, day, plans=p["intent"] in ("plans", "paper"), mode=mode)
    f = rows(days, move, size, mode)
    if not len(f):
        return dict(answer=f"No graded rows for {', '.join(days) or 'that'}.", rows=[], columns=[], params=p)
    title, facts, table = RUN[p["intent"]](f, p)
    facts = dict(days=days if len(days) <= 3 else f"{len(days)} days, {days[-1]} to {days[0]}", move=move, size_pct=size, **facts)
    text, writer = write(q, title, facts, use_model and word)
    return dict(answer=text, writer=writer, router=router, intent=p["intent"], params=p, title=title, days=days, **table)

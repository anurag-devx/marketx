"""API: Stock Desk. Watchlist, on-demand analysis (job), snapshots (latest or any saved / as-of), notes (rich text,
versioned), decisions + review prompts, notes search, compare, AI brief. Everything stays in data/desk/ on this Mac."""
import re
import pandas as pd
from web.api import route, UserError, prices
from core import jobs

SYM = re.compile(r"^[A-Z0-9&\-]{1,20}$")


def _sym(x):
    s = str(x or "").upper().strip()
    if not SYM.match(s):
        raise UserError("pick a stock symbol")
    return s


@route("GET", "desk/watchlist")
def wl_get(p):
    from desk import notes as N
    from desk.analyse import DIR
    w = N.watchlist()
    rows = []
    for s in w:
        snap = N.load_snapshot(s)
        n = N.load(s)
        rows.append(dict(symbol=s, analysed=(snap or {}).get("built"), as_of=(snap or {}).get("as_of"), name=(snap or {}).get("name"),
                         read=(((snap or {}).get("ta") or {}).get("evidence") or {}).get("dir"), notes=bool(n.get("html")), decisions=len(n.get("decisions", []))))
    return dict(items=rows)


@route("POST", "desk/watchlist")
def wl_set(_, body):
    from desk import notes as N
    b = body or {}
    w = N.watchlist()
    if b.get("add"):
        w = w + [_sym(b["add"])]
    if b.get("remove"):
        w = [x for x in w if x != str(b["remove"]).upper()]
    return dict(items=N.set_watchlist(w))


@route("POST", "desk/analyse")
def analyse(_, body):
    b = body or {}
    syms = [_sym(s) for s in (b.get("symbols") or [])][:10]
    if not syms:
        raise UserError("pick at least one stock")
    if any("&" in s for s in syms):
        raise UserError("symbols with '&' can't be analysed from here yet")
    args = [f"symbols={','.join(syms)}"]
    if b.get("as_of"):
        if not re.fullmatch(r"20\d\d-\d\d-\d\d", str(b["as_of"])):
            raise UserError("as-of must be a date")
        args.append(f"as_of={b['as_of']}")
    if jobs.busy("desk-analyse"):
        raise UserError("another job is running in that lane")
    jobs.start("desk-analyse", args)
    return dict(started=True, symbols=syms)


@route("GET", "desk/snapshot")
def snapshot(p):
    from desk import notes as N
    sym = _sym(p.get("symbol"))
    s = N.load_snapshot(sym, p.get("at") or None)
    snaps = N.snapshots(sym)
    if not s:
        return dict(ready=False, symbol=sym, snapshots=snaps, why="Not analysed yet: click Analyse (about 30–60 s).")
    return dict(ready=True, snap=s, snapshots=snaps)


@route("GET", "desk/marketlab")
def desk_ml(p):
    """The influence map for one move / size from a snapshot's Market Lab frames."""
    from desk import notes as N
    from web.r_marketlab import stock_view, _args
    sym = _sym(p.get("symbol"))
    s = N.load_snapshot(sym, p.get("at") or None)
    ml = (s or {}).get("market_lab") or {}
    if not ml.get("ready"):
        return dict(ready=False, why=ml.get("why", "not analysed yet"))
    m, size = _args(p)
    F = {k: pd.DataFrame(v) for k, v in ml["frames"].items()}
    for k in ("cases",):
        if len(F[k]):
            F[k]["case_date"] = pd.to_datetime(F[k]["case_date"])
    return dict(ready=True, **stock_view(F, sym, m, size))


@route("GET", "desk/notes")
def notes_get(p):
    from desk import notes as N
    sym = _sym(p.get("symbol"))
    n = N.load(sym)
    px = prices()
    due = N.due(sym, px) if px is not None else []
    for d in n.get("decisions", []):
        if px is not None and d.get("as_of"):
            d["outcome_now"] = N.outcome(sym, d, px)
    return dict(n, due=due, versions=[dict(at=v["at"], chars=len(v["html"])) for v in n.get("versions", [])][::-1])


@route("POST", "desk/notes")
def notes_save(_, body):
    from desk import notes as N
    b = body or {}
    n = N.save_note(_sym(b.get("symbol")), b.get("html", ""), b.get("tags"))
    return dict(saved=True, updated=n.get("updated"))


@route("GET", "desk/notes/version")
def notes_version(p):
    from desk import notes as N
    n = N.load(_sym(p.get("symbol")))
    v = n.get("versions", [])
    i = int(p.get("i", 0))
    if not 0 <= i < len(v):
        raise UserError("no such version")
    return dict(v[::-1][i])


@route("POST", "desk/decision")
def decision(_, body):
    from desk import notes as N
    b = body or {}
    sym = _sym(b.get("symbol"))
    try:
        d = N.add_decision(sym, b.get("kind"), b.get("note", ""), b.get("horizon_days"), N.load_snapshot(sym))
    except ValueError as e:
        raise UserError(str(e))
    return dict(decision=d)


@route("POST", "desk/review")
def review(_, body):
    from desk import notes as N
    b = body or {}
    sym = _sym(b.get("symbol"))
    px = prices()
    n = N.load(sym)
    d = next((x for x in n.get("decisions", []) if x["id"] == b.get("id")), None)
    if not d:
        raise UserError("decision not found")
    return dict(decision=N.add_review(sym, d["id"], b.get("lesson", ""), N.outcome(sym, d, px) if px is not None else None))


@route("GET", "desk/search")
def search(p):
    from desk import notes as N
    return dict(items=N.search(p.get("q", "")))


@route("GET", "desk/compare")
def compare(p):
    from desk import notes as N
    syms = [_sym(s) for s in str(p.get("symbols", "")).split(",") if s.strip()][:4]
    rows, series, missing = [], {}, []
    for s in syms:
        snap = N.load_snapshot(s)
        if not snap:
            missing.append(s)
            continue
        v = next((x for x in ((snap.get("market_lab") or {}).get("verdicts") or []) if x["move"] == "cc" and x["size"] == 1.0), {}) or {}
        ev = ((snap.get("ta") or {}).get("evidence") or {})
        pl = (((snap.get("ta") or {}).get("plans") or {}).get("swing") or {})
        val = (snap.get("fundamentals") or {}).get("val") or {}
        pr = snap.get("price") or {}
        rows.append(dict(symbol=s, as_of=snap.get("as_of"), sector=snap.get("sector"), close=pr.get("close"), r1m=pr.get("r1m"), r3m=pr.get("r3m"), r1y=pr.get("r1y"),
                         chance=v.get("chance"), usual=v.get("usual"), lean=v.get("lean"), confidence=v.get("confidence"), ta=ev.get("dir"), ta_net=ev.get("net"),
                         pe=val.get("pe"), pb=val.get("pb"), roe=val.get("roe_pct"), rr=max([x.get("rr") or 0 for x in pl.get("sides", [])] or [0]) or None,
                         plan_ok=pl.get("tradeable"), pro=len((snap.get("reasons") or {}).get("pro", [])), con=len((snap.get("reasons") or {}).get("con", []))))
        rel = snap.get("relative") or {}
        if rel.get("t"):
            series[s] = dict(t=rel["t"], y=rel["stock"])
    return dict(rows=rows, series=series, missing=missing)


@route("POST", "desk/brief")
def brief(_, body):
    from desk import notes as N, brief as B
    from web import tasks
    sym = _sym((body or {}).get("symbol"))
    s = N.load_snapshot(sym)
    if not s:
        raise UserError("analyse the stock first")

    def job(log, progress):
        progress(0.3, "Writing the brief from the computed facts ...")
        text, writer = B.write(s, log)
        return dict(text=text, writer=writer)
    try:
        return dict(task=tasks.start("desk-brief", job))
    except RuntimeError as ex:
        raise UserError(str(ex))

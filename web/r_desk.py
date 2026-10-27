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


@route("GET", "desk/quick")
def quick(p):
    """What MarketX already saved about a stock (no Analyse needed): last close, last night's Market Lab numbers, TA and
    fundamentals labels, the plan, and the latest graded result (Tally)."""
    import numpy as np
    from tradeplan import plan as TP
    from marketlab import radar as R, engine as E, tally as T
    sym = _sym(p.get("symbol"))
    out = dict(symbol=sym)
    px = prices()
    g = px[px.symbol == sym].sort_values("date") if px is not None else None
    if g is not None and len(g):
        c = g.close.to_numpy(float)
        ret = lambda n: None if len(c) <= n else float(c[-1] / c[-1 - n] - 1)
        out["price"] = dict(date=str(g.date.iat[-1].date()), close=float(c[-1]), change=ret(1), r1m=ret(21), r3m=ret(63), r1y=ret(252))
    day = R.latest_day()
    rd = R.read("radar", day)
    row = rd[rd.symbol == sym] if len(rd) else rd
    if len(row):
        r = row.iloc[0]
        ml = []
        for m in ("cc", "oc"):
            k = f"{m}_1.0"
            if f"ch_{k}" in r:
                up, nb = float(r[f"up_{k}"]), float(r[f"nb_{k}"])
                ml.append(dict(move=m, chance=float(r[f"ch_{k}"]), usual=float(r[f"us_{k}"]), up_share=up,
                               lean=str(E.lean_of(np.array([up]), np.array([nb]))[0])))
        out["market_lab"] = dict(day=day, rows=ml, sector=r.get("sector"))
    saved = TP.load(day) or {}
    pl = (saved.get("plans") or {}).get(sym)
    if pl:
        legs = lambda m: [x for x in (pl.get(m) or {}).get("legs", []) if x.get("price") is not None]
        out["plan"] = dict(day=saved.get("day"), ta=pl.get("ta"), fund=pl.get("fund"), patterns=pl.get("patterns", [])[:4],
                           intraday=dict(lean=(pl.get("intraday") or {}).get("lean"), legs=legs("intraday")),
                           swing=dict(lean=(pl.get("swing") or {}).get("lean"), legs=legs("swing")))
    try:
        from core import valuation as V, bhavcopy as BH
        out["valuation"] = V.latest(sym)
        out["delivery"] = BH.delivery_read(sym) if BH.DAILY.exists() else None
    except Exception:
        pass
    graded = [x["day"] for x in T.days() if x["graded"]]
    if graded:
        b = T.build(graded[0])
        hit = next((x for x in b.get("rows", []) if x["symbol"] == sym), None)
        if hit:
            out["tally"] = dict(day=graded[0], for_day=b.get("for_day"), **{k: hit.get(k) for k in ("pick", "chance", "usual", "lean", "ret", "hit", "lean_right", "status", "side", "entry", "exit", "r")})
    return out


@route("GET", "desk/diff")
def diff(p):
    """A saved analysis next to the latest one: what changed (Market Lab, technical read, reasons, behaviour) and how the
    price moved in between. Nothing is recomputed: both are the snapshots as they were saved."""
    from desk import notes as N
    sym = _sym(p.get("symbol"))
    a = N.load_snapshot(sym, p.get("at") or None)
    b = N.load_snapshot(sym)
    if not a or not b:
        raise UserError("need a saved analysis and a latest one")
    def ml(s, m):
        v = next((x for x in ((s.get("market_lab") or {}).get("verdicts") or []) if x.get("move") == m and float(x.get("size", 0)) == 1.0), None)
        return v or {}
    def ev(s):
        return (((s.get("ta") or {}).get("evidence")) or {})
    rows = []
    def add(label, x, y, kind="text"):
        rows.append(dict(label=label, then=x, now=y, kind=kind, changed=x != y))
    add("As of", a.get("as_of"), b.get("as_of"))
    add("Close", (a.get("price") or {}).get("close"), (b.get("price") or {}).get("close"), "num")
    for m, lab in (("cc", "close-to-close"), ("oc", "open-to-close")):
        x, y = ml(a, m), ml(b, m)
        add(f"Market Lab ≥1% {lab}: chance", x.get("chance"), y.get("chance"), "pct")
        add(f"Market Lab ≥1% {lab}: lean", x.get("lean"), y.get("lean"), "dir")
        add(f"Market Lab ≥1% {lab}: confidence", x.get("confidence"), y.get("confidence"))
    add("Chart read", ev(a).get("dir"), ev(b).get("dir"), "dir")
    add("Chart read: net score", ev(a).get("net"), ev(b).get("net"), "num")
    ia, ib = a.get("intraday") or {}, b.get("intraday") or {}
    add("Sessions crossing ±1%", ia.get("crossed_pct"), ib.get("crossed_pct"), "pct")
    add("Held beyond 1% to the close", ia.get("held_pct"), ib.get("held_pct"), "pct")
    ra, rb = a.get("reasons") or {}, b.get("reasons") or {}
    txt = lambda r, k: {x.get("text") for x in r.get(k, [])}
    reasons = dict(pro_new=sorted(txt(rb, "pro") - txt(ra, "pro")), pro_gone=sorted(txt(ra, "pro") - txt(rb, "pro")),
                   con_new=sorted(txt(rb, "con") - txt(ra, "con")), con_gone=sorted(txt(ra, "con") - txt(rb, "con")))
    ca, cb = (a.get("price") or {}).get("close"), (b.get("price") or {}).get("close")
    moved = None if not (ca and cb) else cb / ca - 1
    return dict(symbol=sym, then=dict(at=p.get("at"), built=a.get("built"), as_of=a.get("as_of")), now=dict(built=b.get("built"), as_of=b.get("as_of")),
                moved=moved, rows=rows, reasons=reasons)


@route("GET", "spark")
def spark(p):
    """Last n daily closes per stock for the small trend lines in lists (default 20 sessions; at most 600 stocks)."""
    px = prices()
    syms = [s for s in str(p.get("symbols") or "").upper().split(",") if SYM.match(s)][:600]
    try:
        n = max(5, min(120, int(p.get("n") or 20)))
    except ValueError:
        n = 20
    if px is None or not syms:
        return dict(items={})
    g = px[px.symbol.isin(syms)].sort_values("date").groupby("symbol").tail(n)
    return dict(items={s: [round(float(x), 2) for x in v.close] for s, v in g.groupby("symbol")})
